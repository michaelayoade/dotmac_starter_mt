"""Postgres proof for `mod_agreements`: migration, grants, isolation, append-only.

Like `tests/test_application_directory_isolation.py`, this provisions its OWN
scratch database and composes the module's lineage explicitly, because the
reference assembly deliberately does not compose `dotmac-commercial-agreements`:
the starter is a target application, and only a vendor control plane holds a
vendor↔operator agreement (ADR-0033 § 7). Adding it to `app/assembly.py` or the
shipped `alembic.ini` would put `mod_agreements` into every starter deployment.

**On the platform plane the REVOKE is the isolation**, and it is checked as
strictly here as a policy is on the tenant side (hard rule 27). Two halves, both
of which have to hold:

1. `app_user` — the tenant data-plane role — can reach nothing.
2. `platform_api` — the ONLINE role — can reach everything it needs. Declared
   and unusable is a violation too, and it is the half a REVOKE-only test would
   miss entirely.

Requires real Postgres (`make test-db-up` / `make test-integration`). SQLite
cannot enforce a grant, so none of this belongs in `tests/unit`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
AGREEMENT_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-commercial-agreements/src/dotmac_commercial_agreements"
    / "migrations/versions"
)

SCHEMA = "mod_agreements"
TABLES = ("agreements", "agreement_lines", "agreement_events")

#: The seven table privileges. A revoke that covers six is not a revoke.
ALL_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

#: The four that make a request path usable. `REFERENCES`, `TRIGGER` and
#: `TRUNCATE` alone do not — that is the "declared and unusable" case.
ROW_DML = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the platform canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(scope="module")
def migrated_scratch() -> Iterator[tuple[str, str, str]]:
    """Yield `(admin_url, platform_api_url, app_user_url)` at the composed head.

    Module-scoped: building a database and running the whole kernel lineage per
    test would dominate the run, and every test here is read-only or writes rows
    it cleans up by being in a rolled-back transaction.
    """
    superuser = _superuser_url()
    name = f"agreements_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        # A MODULE lineage creates its own schema, and `CREATE SCHEMA` needs
        # CREATE on the DATABASE — not merely ownership of `public`.
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        for role in ("app_user", "platform_api"):
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO {role}'))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {AGREEMENT_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        # From an EMPTY database to the composed head. This is the migration
        # proof: the lineage's prerequisite verification runs for real against
        # a catalog the kernel lineage built moments earlier.
        command.upgrade(cfg, "heads")

        yield (
            admin_url,
            _url_for(superuser, name, user="platform_api"),
            _url_for(superuser, name, user="app_user"),
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


def _has_privilege(url: str, table: str, privilege: str, *, role: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": role, "t": f"mod_agreements.{table}", "p": privilege},
                ).scalar()
            )
    finally:
        engine.dispose()


# ── Migration from empty ────────────────────────────────────────────────────


class TestTheLineageBuildsFromAnEmptyDatabase:
    def test_the_schema_and_all_three_tables_exist(self, migrated_scratch) -> None:
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                for table in TABLES:
                    assert (
                        conn.execute(
                            text("SELECT to_regclass(:t)"),
                            {"t": f"mod_agreements.{table}"},
                        ).scalar()
                        is not None
                    ), table
        finally:
            engine.dispose()

    def test_no_table_carries_a_tenant_column(self, migrated_scratch) -> None:
        """A platform table with a `tenant_id` has picked the wrong plane."""
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND column_name = 'tenant_id'"
                    ),
                    {"s": SCHEMA},
                ).all()
            assert not rows, rows
        finally:
            engine.dispose()

    def test_no_table_has_row_level_security(self, migrated_scratch) -> None:
        """Not even ENABLEd-with-no-policy, which denies every row to the
        control plane while reading as protected (hard rule 27)."""
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                for table in TABLES:
                    enabled, forced = conn.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE oid = CAST(:t AS regclass)"
                        ),
                        {"t": f"mod_agreements.{table}"},
                    ).one()
                    assert not enabled and not forced, table
        finally:
            engine.dispose()

    def test_no_foreign_key_leaves_the_module_schema(self, migrated_scratch) -> None:
        """ADR-0006 D1: a cross-lineage FK splices two independently released
        lineages and makes either un-releasable without the other."""
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                foreign = conn.execute(
                    text(
                        """
                        SELECT c.conname, tn.nspname
                        FROM pg_constraint c
                        JOIN pg_class t  ON t.oid  = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        JOIN pg_class tt ON tt.oid = c.confrelid
                        JOIN pg_namespace tn ON tn.oid = tt.relnamespace
                        WHERE c.contype = 'f' AND n.nspname = :s
                          AND tn.nspname <> :s
                        """
                    ),
                    {"s": SCHEMA},
                ).all()
            assert not foreign, foreign
        finally:
            engine.dispose()


# ── Isolation: the revoke half ──────────────────────────────────────────────


class TestTheTenantAppRoleCanReachNothing:
    @pytest.mark.parametrize("table", TABLES)
    @pytest.mark.parametrize("privilege", ALL_PRIVILEGES)
    def test_app_user_holds_no_privilege(
        self, migrated_scratch, table: str, privilege: str
    ) -> None:
        """All seven privileges, not the four anyone remembers. A revoke that
        covers six is not a revoke."""
        admin_url, _, _ = migrated_scratch
        assert not _has_privilege(admin_url, table, privilege, role="app_user")

    @pytest.mark.parametrize("table", TABLES)
    def test_app_user_holds_no_column_level_privilege(
        self, migrated_scratch, table: str
    ) -> None:
        """Column grants survive a table-level REVOKE that names only tables."""
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT column_name, privilege_type "
                        "FROM information_schema.column_privileges "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND grantee = 'app_user'"
                    ),
                    {"s": SCHEMA, "t": table},
                ).all()
            assert not rows, rows
        finally:
            engine.dispose()

    def test_a_real_select_as_app_user_is_refused(self, migrated_scratch) -> None:
        """The privilege catalogue and a real connection can disagree; this is
        the one that matters to a request."""
        _, _, app_user_url = migrated_scratch
        engine = create_engine(app_user_url)
        try:
            with (
                engine.connect() as conn,
                pytest.raises((DBAPIError, ProgrammingError)),
            ):
                conn.execute(text("SELECT 1 FROM mod_agreements.agreements"))
        finally:
            engine.dispose()


# ── Isolation: the reachability half ────────────────────────────────────────


class TestTheOnlinePlatformRoleCanActuallyWork:
    """Declared and unusable is a violation too, and a REVOKE-only suite misses
    it completely — every assertion above would still pass if `platform_api`
    had been granted nothing at all."""

    @pytest.mark.parametrize("table", TABLES)
    def test_platform_api_holds_at_least_one_row_dml_privilege(
        self, migrated_scratch, table: str
    ) -> None:
        admin_url, _, _ = migrated_scratch
        held = [
            p
            for p in ROW_DML
            if _has_privilege(admin_url, table, p, role="platform_api")
        ]
        assert held, f"platform_api cannot reach {table} at all"

    def test_platform_api_can_insert_an_agreement_and_read_it_back(
        self, migrated_scratch
    ) -> None:
        _, platform_url, _ = migrated_scratch
        engine = create_engine(platform_url)
        agreement_id = uuid.uuid4()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreements ("
                        " id, reference, agreement_family_id, agreement_version,"
                        " counterparty_ref, agreement_type, status,"
                        " effective_date, expiry_date, record_version"
                        ") VALUES (:id, :ref, :fam, 1, 'acme', 'oem', 'draft',"
                        " DATE '2026-09-01', DATE '2027-08-31', 1)"
                    ),
                    {
                        "id": agreement_id,
                        "ref": f"AGR-{uuid.uuid4().hex[:8]}",
                        "fam": uuid.uuid4(),
                    },
                )
                found = conn.execute(
                    text("SELECT status FROM mod_agreements.agreements WHERE id = :id"),
                    {"id": agreement_id},
                ).scalar()
            assert found == "draft"
        finally:
            engine.dispose()

    def test_platform_api_may_update_the_header_but_not_a_line(
        self, migrated_scratch
    ) -> None:
        """The lifecycle lives on the header, so UPDATE has to exist there. The
        lines are frozen at proposal, which is why they get none."""
        admin_url, _, _ = migrated_scratch
        assert _has_privilege(admin_url, "agreements", "UPDATE", role="platform_api")
        assert not _has_privilege(
            admin_url, "agreement_lines", "UPDATE", role="platform_api"
        )


# ── Append-only history ─────────────────────────────────────────────────────


class TestTheHistoryIsAppendOnlyAgainstEveryRole:
    """A service rule cannot police a path that never calls the service, and an
    evidence history an administrator can rewrite is not evidence."""

    @pytest.fixture
    def seeded(self, migrated_scratch) -> tuple[str, uuid.UUID]:
        admin_url, _, _ = migrated_scratch
        agreement_id, family_id = uuid.uuid4(), uuid.uuid4()
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreements ("
                        " id, reference, agreement_family_id, agreement_version,"
                        " counterparty_ref, agreement_type, status,"
                        " effective_date, expiry_date, record_version"
                        ") VALUES (:id, :ref, :fam, 1, 'acme', 'oem', 'draft',"
                        " DATE '2026-09-01', DATE '2027-08-31', 1)"
                    ),
                    {
                        "id": agreement_id,
                        "ref": f"AGR-{uuid.uuid4().hex[:8]}",
                        "fam": family_id,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreement_events ("
                        " id, agreement_id, sequence, event_type, to_status,"
                        " command_id"
                        ") VALUES (:id, :aid, 1, 'agreement.proposed.v1',"
                        " 'proposed', 'cmd-1')"
                    ),
                    {"id": uuid.uuid4(), "aid": agreement_id},
                )
        finally:
            engine.dispose()
        return admin_url, agreement_id

    def test_app_admin_cannot_update_a_history_row(self, seeded) -> None:
        """`app_admin` legitimately holds full DML on the other two tables. The
        trigger is the only place this rule holds for it too."""
        admin_url, agreement_id = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
                conn.execute(
                    text(
                        "UPDATE mod_agreements.agreement_events SET reason = 'edited' "
                        "WHERE agreement_id = :aid"
                    ),
                    {"aid": agreement_id},
                )
        finally:
            engine.dispose()

    def test_app_admin_cannot_delete_a_history_row(self, seeded) -> None:
        admin_url, agreement_id = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
                conn.execute(
                    text(
                        "DELETE FROM mod_agreements.agreement_events "
                        "WHERE agreement_id = :aid"
                    ),
                    {"aid": agreement_id},
                )
        finally:
            engine.dispose()

    def test_deleting_the_agreement_cannot_launder_a_history_rewrite(
        self, seeded
    ) -> None:
        """`ondelete="RESTRICT"` closes the hole from the other side. Without
        it, "delete then re-create" removes the history through a path the
        trigger never sees."""
        admin_url, agreement_id = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text("DELETE FROM mod_agreements.agreements WHERE id = :id"),
                    {"id": agreement_id},
                )
        finally:
            engine.dispose()

    def test_appending_a_further_row_still_works(self, seeded) -> None:
        """The trigger must refuse rewrites without refusing the append that
        every compensating transition depends on — a guard that blocked INSERT
        would make the whole module unusable while passing every test above."""
        admin_url, agreement_id = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreement_events ("
                        " id, agreement_id, sequence, event_type, to_status,"
                        " command_id"
                        ") VALUES (:id, :aid, 2, 'agreement.approved.v1',"
                        " 'approved', 'cmd-2')"
                    ),
                    {"id": uuid.uuid4(), "aid": agreement_id},
                )
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM mod_agreements.agreement_events "
                        "WHERE agreement_id = :aid"
                    ),
                    {"aid": agreement_id},
                ).scalar()
            assert count == 2
        finally:
            engine.dispose()


# ── Constraints hold against raw SQL ────────────────────────────────────────


class TestTheConstraintsHoldWithoutTheService:
    """Every rule below is also enforced in `service.py`. These prove the
    database enforces them too — the service cannot police a path that never
    calls it."""

    @pytest.fixture
    def admin_url(self, migrated_scratch) -> str:
        return migrated_scratch[0]

    def _insert_agreement(self, conn, **overrides: object) -> uuid.UUID:
        params: dict[str, object] = {
            "id": uuid.uuid4(),
            "ref": f"AGR-{uuid.uuid4().hex[:8]}",
            "fam": uuid.uuid4(),
            "version": 1,
            "record_version": 1,
            "effective": "2026-09-01",
            "expiry": "2027-08-31",
        }
        params.update(overrides)
        conn.execute(
            text(
                "INSERT INTO mod_agreements.agreements ("
                " id, reference, agreement_family_id, agreement_version,"
                " counterparty_ref, agreement_type, status, effective_date,"
                " expiry_date, record_version"
                ") VALUES (:id, :ref, :fam, :version, 'acme', 'oem', 'draft',"
                " CAST(:effective AS date), CAST(:expiry AS date), :record_version)"
            ),
            params,
        )
        return params["id"]  # type: ignore[return-value]

    def test_a_duplicate_reference_is_refused(self, admin_url: str) -> None:
        engine = create_engine(admin_url)
        reference = f"AGR-{uuid.uuid4().hex[:8]}"
        try:
            with engine.begin() as conn:
                self._insert_agreement(conn, ref=reference)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                self._insert_agreement(conn, ref=reference)
        finally:
            engine.dispose()

    def test_a_duplicate_family_version_is_refused(self, admin_url: str) -> None:
        """One version per family, so an amendment cannot fork the chain."""
        engine = create_engine(admin_url)
        family_id = uuid.uuid4()
        try:
            with engine.begin() as conn:
                self._insert_agreement(conn, fam=family_id, version=2)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                self._insert_agreement(conn, fam=family_id, version=2)
        finally:
            engine.dispose()

    def test_an_expiry_before_the_effective_date_is_refused(
        self, admin_url: str
    ) -> None:
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError):
                self._insert_agreement(
                    conn, effective="2027-01-01", expiry="2026-01-01"
                )
        finally:
            engine.dispose()

    def test_a_non_positive_line_quantity_is_refused(self, admin_url: str) -> None:
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                agreement_id = self._insert_agreement(conn)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreement_lines ("
                        " id, agreement_id, line_no, product_code,"
                        " capability_code, quantity, unit_amount,"
                        " unit_currency_code"
                        ") VALUES (:id, :aid, 1, 'p', 'c', 0, '1.00', 'NGN')"
                    ),
                    {"id": uuid.uuid4(), "aid": agreement_id},
                )
        finally:
            engine.dispose()

    def test_a_duplicate_history_sequence_is_refused(self, admin_url: str) -> None:
        """Dense per-agreement sequencing is what makes a gap detectable; a
        duplicate would let two transitions claim the same position."""
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                agreement_id = self._insert_agreement(conn)
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreement_events ("
                        " id, agreement_id, sequence, event_type, to_status,"
                        " command_id"
                        ") VALUES (:id, :aid, 1, 'e', 'proposed', 'cmd-1')"
                    ),
                    {"id": uuid.uuid4(), "aid": agreement_id},
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_agreements.agreement_events ("
                        " id, agreement_id, sequence, event_type, to_status,"
                        " command_id"
                        ") VALUES (:id, :aid, 1, 'e2', 'approved', 'cmd-2')"
                    ),
                    {"id": uuid.uuid4(), "aid": agreement_id},
                )
        finally:
            engine.dispose()
