"""Postgres proof for `mod_licensing`: migration, grants, isolation, append-only.

Provisions its own scratch database and composes the module's lineage explicitly,
because the reference assembly deliberately does not compose `dotmac-licensing`.
For this module that omission is stronger than the usual "no consumer here": a
data plane installing licence ISSUANCE would put the thing that decides what a
deployment may do inside the deployment it decides about (ADR-0057 § 7).

**On the platform plane the REVOKE is the isolation**, checked as strictly as a
policy is on the tenant side (hard rule 27), in both directions:

1. `app_user` reaches nothing — all seven privileges, plus column-level.
2. `platform_api` reaches everything it needs. Declared-and-unusable is a
   violation too, and a REVOKE-only suite would miss it entirely.

Requires real Postgres (`make test-db-up` / `make test-integration`).
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
LICENSING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-licensing/src/dotmac_licensing" / "migrations/versions"
)

SCHEMA = "mod_licensing"
TABLES = (
    "signing_keys",
    "licences",
    "licence_issuances",
    "licence_acknowledgements",
    "revocations",
    "revocation_lists",
)
#: The three whose whole value is that nobody can adjust them.
EVIDENCE_TABLES = ("licence_acknowledgements", "revocations", "revocation_lists")

#: All seven. A revoke that covers six is not a revoke.
ALL_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
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
    """`(admin_url, platform_api_url, app_user_url)` at the composed head."""
    superuser = _superuser_url()
    name = f"licensing_{uuid.uuid4().hex[:12]}"
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {LICENSING_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        # From an EMPTY database to the composed head, so the lineage's
        # prerequisite verification runs for real against a catalog the kernel
        # lineage built moments earlier.
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
                    {"r": role, "t": f"mod_licensing.{table}", "p": privilege},
                ).scalar()
            )
    finally:
        engine.dispose()


# ── Migration from empty ────────────────────────────────────────────────────


class TestTheLineageBuildsFromAnEmptyDatabase:
    def test_every_table_exists(self, migrated_scratch) -> None:
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                for table in TABLES:
                    assert (
                        conn.execute(
                            text("SELECT to_regclass(:t)"),
                            {"t": f"mod_licensing.{table}"},
                        ).scalar()
                        is not None
                    ), table
        finally:
            engine.dispose()

    def test_there_is_no_private_key_column_anywhere_in_the_schema(
        self, migrated_scratch
    ) -> None:
        """The property the whole module rests on, asserted against the LIVE
        catalog rather than the model layer. A database dump cannot leak what
        the schema has no column for."""
        admin_url, _, _ = migrated_scratch
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name, column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema = :s AND ("
                        "  column_name LIKE '%private%'"
                        "  OR column_name LIKE '%secret%'"
                        "  OR column_name LIKE '%passphrase%'"
                        "  OR column_name LIKE '%key_material%')"
                    ),
                    {"s": SCHEMA},
                ).all()
            assert not rows, rows
        finally:
            engine.dispose()

    def test_no_table_carries_a_tenant_column(self, migrated_scratch) -> None:
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
                        {"t": f"mod_licensing.{table}"},
                    ).one()
                    assert not enabled and not forced, table
        finally:
            engine.dispose()

    def test_no_foreign_key_leaves_the_module_schema(self, migrated_scratch) -> None:
        """ADR-0006 D1. A licence must stay verifiable after the agreement row
        is archived and the allocation's retention has passed."""
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


# ── Isolation ───────────────────────────────────────────────────────────────


class TestTheTenantAppRoleCanReachNothing:
    @pytest.mark.parametrize("table", TABLES)
    @pytest.mark.parametrize("privilege", ALL_PRIVILEGES)
    def test_app_user_holds_no_privilege(
        self, migrated_scratch, table: str, privilege: str
    ) -> None:
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

    def test_a_real_select_on_the_key_registry_as_app_user_is_refused(
        self, migrated_scratch
    ) -> None:
        """The one that would matter most if it leaked: even the public-key
        registry is not a data plane's to read."""
        _, _, app_user_url = migrated_scratch
        engine = create_engine(app_user_url)
        try:
            with (
                engine.connect() as conn,
                pytest.raises((DBAPIError, ProgrammingError)),
            ):
                conn.execute(text("SELECT 1 FROM mod_licensing.signing_keys"))
        finally:
            engine.dispose()


class TestTheOnlinePlatformRoleCanActuallyWork:
    @pytest.mark.parametrize("table", TABLES)
    def test_platform_api_holds_at_least_one_row_dml_privilege(
        self, migrated_scratch, table: str
    ) -> None:
        """Declared and unusable is a violation too, and a REVOKE-only suite
        would miss it: every assertion above would still pass if `platform_api`
        had been granted nothing at all."""
        admin_url, _, _ = migrated_scratch
        held = [
            p
            for p in ROW_DML
            if _has_privilege(admin_url, table, p, role="platform_api")
        ]
        assert held, f"platform_api cannot reach {table} at all"

    def test_platform_api_may_update_the_lifecycle_but_not_a_lineage(
        self, migrated_scratch
    ) -> None:
        """The lifecycle lives on the issuance, so UPDATE has to exist there. A
        lineage's identity does not change, so it gets none."""
        admin_url, _, _ = migrated_scratch
        assert _has_privilege(
            admin_url, "licence_issuances", "UPDATE", role="platform_api"
        )
        assert not _has_privilege(admin_url, "licences", "UPDATE", role="platform_api")

    def test_platform_api_may_rotate_a_key(self, migrated_scratch) -> None:
        admin_url, _, _ = migrated_scratch
        assert _has_privilege(admin_url, "signing_keys", "UPDATE", role="platform_api")


# ── Append-only evidence ────────────────────────────────────────────────────


class TestTheEvidenceTablesAreAppendOnlyAgainstEveryRole:
    @pytest.fixture
    def seeded(self, migrated_scratch) -> tuple[str, uuid.UUID, uuid.UUID]:
        admin_url, _, _ = migrated_scratch
        licence_id, issuance_id = uuid.uuid4(), uuid.uuid4()
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licences ("
                        " id, subject_ref, product_code, generation"
                        ") VALUES (:id, :subject, 'dotmac_sub', 1)"
                    ),
                    {"id": licence_id, "subject": f"acme-{uuid.uuid4().hex[:6]}"},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licence_issuances ("
                        " id, licence_id, version, agreement_ref, allocation_ref,"
                        " digest, key_id, envelope, status, record_version,"
                        " grace_days"
                        ") VALUES (:id, :lid, 1, 'agr-1', :alloc, :digest, 'k1',"
                        " CAST('{}' AS jsonb), 'issued', 1, 0)"
                    ),
                    {
                        "id": issuance_id,
                        "lid": licence_id,
                        "alloc": f"alloc-{uuid.uuid4().hex[:8]}",
                        "digest": f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licence_acknowledgements ("
                        " id, issuance_id, licence_version, digest, outcome,"
                        " reported_at, reported_deployment_ref"
                        ") VALUES (:id, :iid, 1, 'sha256:x', 'applied', now(), 'd1')"
                    ),
                    {"id": uuid.uuid4(), "iid": issuance_id},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.revocations ("
                        " id, licence_id, reason"
                        ") VALUES (:id, :lid, 'breach')"
                    ),
                    {"id": uuid.uuid4(), "lid": licence_id},
                )
        finally:
            engine.dispose()
        return admin_url, licence_id, issuance_id

    @pytest.mark.parametrize("table", ["licence_acknowledgements", "revocations"])
    def test_app_admin_cannot_update_an_evidence_row(self, seeded, table: str) -> None:
        """`app_admin` legitimately holds full DML on the other three tables. The
        trigger is the only place this rule holds for it too."""
        admin_url, _, _ = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
                conn.execute(
                    text(
                        f"UPDATE mod_licensing.{table} "  # noqa: S608 - fixed literal
                        "SET reason = 'edited'"
                    )
                )
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table", ["licence_acknowledgements", "revocations"])
    def test_app_admin_cannot_delete_an_evidence_row(self, seeded, table: str) -> None:
        """A deletable revocation row is an un-revoke by another name — exactly
        what the cumulative rule exists to make impossible."""
        admin_url, _, _ = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
                conn.execute(
                    text(f"DELETE FROM mod_licensing.{table}")  # noqa: S608
                )
        finally:
            engine.dispose()

    def test_deleting_the_licence_cannot_launder_a_revocation_away(
        self, seeded
    ) -> None:
        """`ondelete="RESTRICT"` closes the hole from the other side: without
        it, "delete then re-create" removes the evidence through a path the
        trigger never sees."""
        admin_url, licence_id, _ = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text("DELETE FROM mod_licensing.licences WHERE id = :id"),
                    {"id": licence_id},
                )
        finally:
            engine.dispose()

    def test_appending_a_further_evidence_row_still_works(self, seeded) -> None:
        """The trigger must refuse rewrites without refusing the append every
        later report depends on — a guard that blocked INSERT would make the
        module unusable while passing every test above."""
        admin_url, _, issuance_id = seeded
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licence_acknowledgements ("
                        " id, issuance_id, licence_version, digest, outcome,"
                        " reported_at, reported_deployment_ref"
                        ") VALUES (:id, :iid, 1, 'sha256:x', 'rejected', now(),"
                        " 'd2')"
                    ),
                    {"id": uuid.uuid4(), "iid": issuance_id},
                )
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM mod_licensing.licence_acknowledgements "
                        "WHERE issuance_id = :iid"
                    ),
                    {"iid": issuance_id},
                ).scalar()
            assert count == 2
        finally:
            engine.dispose()


# ── Constraints hold against raw SQL ────────────────────────────────────────


class TestTheConstraintsHoldWithoutTheService:
    """Every rule below is also enforced in `service.py`. These prove the
    database enforces them too — the service cannot police a path that never
    calls it, and two of them prevent the same entitlement being authorised
    twice."""

    @pytest.fixture
    def admin_url(self, migrated_scratch) -> str:
        return migrated_scratch[0]

    def _licence(self, conn) -> uuid.UUID:  # type: ignore[no-untyped-def]
        licence_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_licensing.licences ("
                " id, subject_ref, product_code, generation"
                ") VALUES (:id, :subject, 'p', 1)"
            ),
            {"id": licence_id, "subject": f"s-{uuid.uuid4().hex[:8]}"},
        )
        return licence_id

    def _issuance(self, conn, licence_id, **overrides) -> None:  # type: ignore[no-untyped-def]
        params: dict[str, object] = {
            "id": uuid.uuid4(),
            "lid": licence_id,
            "version": 1,
            "alloc": f"alloc-{uuid.uuid4().hex[:10]}",
            "digest": f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}",
            "grace": 0,
        }
        params.update(overrides)
        conn.execute(
            text(
                "INSERT INTO mod_licensing.licence_issuances ("
                " id, licence_id, version, agreement_ref, allocation_ref,"
                " digest, key_id, envelope, status, record_version, grace_days"
                ") VALUES (:id, :lid, :version, 'agr', :alloc, :digest, 'k',"
                " CAST('{}' AS jsonb), 'issued', 1, :grace)"
            ),
            params,
        )

    def test_two_issuances_cannot_claim_one_allocation(self, admin_url: str) -> None:
        """The same entitlement authorised twice is what an idempotent issuer
        must make impossible."""
        engine = create_engine(admin_url)
        allocation = f"alloc-{uuid.uuid4().hex[:10]}"
        try:
            with engine.begin() as conn:
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, alloc=allocation)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, alloc=allocation)
        finally:
            engine.dispose()

    def test_two_issuances_cannot_claim_one_digest(self, admin_url: str) -> None:
        """Either a duplicate issuance or a digest computed over the wrong
        bytes. Both must fail here rather than at a receiver."""
        engine = create_engine(admin_url)
        digest = f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}"
        try:
            with engine.begin() as conn:
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, digest=digest)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, digest=digest)
        finally:
            engine.dispose()

    def test_two_issuances_cannot_claim_one_version_of_a_lineage(
        self, admin_url: str
    ) -> None:
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, version=1)
            with engine.begin() as conn, pytest.raises(DBAPIError):
                self._issuance(conn, licence_id, version=1)
        finally:
            engine.dispose()

    def test_a_duplicate_lineage_is_refused(self, admin_url: str) -> None:
        """One lineage per (subject, product, generation) — the resolver depends
        on it to find the current generation."""
        engine = create_engine(admin_url)
        subject = f"s-{uuid.uuid4().hex[:8]}"
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licences ("
                        " id, subject_ref, product_code, generation"
                        ") VALUES (:id, :s, 'p', 1)"
                    ),
                    {"id": uuid.uuid4(), "s": subject},
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.licences ("
                        " id, subject_ref, product_code, generation"
                        ") VALUES (:id, :s, 'p', 1)"
                    ),
                    {"id": uuid.uuid4(), "s": subject},
                )
        finally:
            engine.dispose()

    def test_a_lineage_cannot_be_revoked_twice(self, admin_url: str) -> None:
        """Revoking twice is idempotent rather than a duplicated fact."""
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                licence_id = self._licence(conn)
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.revocations (id, licence_id, reason)"
                        " VALUES (:id, :lid, 'breach')"
                    ),
                    {"id": uuid.uuid4(), "lid": licence_id},
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.revocations (id, licence_id, reason)"
                        " VALUES (:id, :lid, 'again')"
                    ),
                    {"id": uuid.uuid4(), "lid": licence_id},
                )
        finally:
            engine.dispose()

    def test_a_negative_grace_period_is_refused(self, admin_url: str) -> None:
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError):
                licence_id = self._licence(conn)
                self._issuance(conn, licence_id, grace=-1)
        finally:
            engine.dispose()

    def test_a_duplicate_revocation_list_version_is_refused(
        self, admin_url: str
    ) -> None:
        """Two lists at one version means the fleet cannot tell which it holds."""
        engine = create_engine(admin_url)
        version = 900 + int(uuid.uuid4().int % 90)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.revocation_lists ("
                        " id, list_version, digest, key_id, entry_count, envelope"
                        ") VALUES (:id, :v, 'sha256:a', 'k', 0,"
                        " CAST('{}' AS jsonb))"
                    ),
                    {"id": uuid.uuid4(), "v": version},
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_licensing.revocation_lists ("
                        " id, list_version, digest, key_id, entry_count, envelope"
                        ") VALUES (:id, :v, 'sha256:b', 'k', 0,"
                        " CAST('{}' AS jsonb))"
                    ),
                    {"id": uuid.uuid4(), "v": version},
                )
        finally:
            engine.dispose()
