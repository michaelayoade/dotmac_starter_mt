"""Postgres isolation canaries for the import run ledger.

The reference assembly builds but does not install ``dotmac-imports``, so this
composes its lineage in a scratch database and drives the assertions as the
online ``app_user`` role. SQLite cannot prove row-level security, so the unit
suite deliberately proves nothing about tenancy.

Both source products' ledgers have no tenant column at all, which is exactly why
these canaries exist: the isolation here was added by the extraction rather than
ported by it, and nothing upstream can vouch for it.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from dotmac_imports import (
    ColumnMapping,
    FieldSet,
    FieldSpec,
    ImportIssue,
    RunStatus,
    SourceDocument,
    create_dry_run,
    get_run,
    promote,
    validate_next_chunk,
)
from dotmac_imports.models import ImportRunRow
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
IMPORTS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-imports/src/dotmac_imports/migrations/versions"
)
_RUNS = "mod_imports.import_runs"
_ROWS = "mod_imports.import_run_rows"
_PARTITIONS = "mod_imports.import_partitions"
_DIGEST = "a" * 64
_ROW_FINGERPRINT = "b" * 64


class _AlwaysValid:
    def validate(self, row: Mapping[str, str]) -> Sequence[ImportIssue]:
        return ()


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"imports_rls_{uuid.uuid4().hex[:12]}"
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

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {IMPORTS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
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


def _insert_run(conn, tenant_id: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    run_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_imports.import_runs ("
            "id, tenant_id, kind, status, dry_run, source_file_id, "
            "source_checksum_sha256, source_layout, source_delimiter, "
            "source_encoding"
            ") VALUES ("
            ":id, :tenant, 'receipts', 'dry_run_ready', true, :file, "
            ":digest, 'csv', ',', 'utf-8'"
            ")"
        ),
        {
            "id": run_id,
            "tenant": tenant_id,
            "file": uuid.uuid4(),
            "digest": _DIGEST,
        },
    )
    conn.execute(
        text(
            "INSERT INTO mod_imports.import_run_rows ("
            "id, tenant_id, run_id, row_number, row_fingerprint_sha256, status"
            ") VALUES (:id, :tenant, :run, 1, :fingerprint, 'ok')"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": tenant_id,
            "run": run_id,
            "fingerprint": _ROW_FINGERPRINT,
        },
    )
    conn.execute(
        text(
            "INSERT INTO mod_imports.import_partitions ("
            "id, tenant_id, run_id, ordinal, start_row, row_count, "
            "partition_file_id, partition_checksum_sha256, byte_size, status"
            ") VALUES ("
            ":id, :tenant, :run, 0, 0, 1, :file, :digest, 64, 'completed'"
            ")"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": tenant_id,
            "run": run_id,
            "file": uuid.uuid4(),
            "digest": _DIGEST,
        },
    )
    return run_id


def _seed_two_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                _insert_run(conn, tenant_id)
    finally:
        engine.dispose()
    return tenant_a, tenant_b


@pytest.mark.parametrize(
    ("schema_table", "table"),
    (
        (_RUNS, "import_runs"),
        (_ROWS, "import_run_rows"),
        (_PARTITIONS, "import_partitions"),
    ),
)
def test_rls_is_enabled_forced_and_has_the_tenant_policy(
    migrated_scratch: tuple[str, str], schema_table: str, table: str
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table_name AS regclass)"
                ),
                {"table_name": schema_table},
            ).one()
            assert enabled
            assert forced
            policies = list(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = 'mod_imports' AND tablename = :t"
                    ),
                    {"t": table},
                ).scalars()
            )
            assert f"{table}_tenant_isolation" in policies
    finally:
        engine.dispose()


def test_the_schema_passes_the_kernel_live_catalog_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_imports.manifest import module
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert audit_live_schemas(conn, registry) == ()
    finally:
        engine.dispose()


def test_online_role_sees_only_the_bound_tenants_runs(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            runs = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_imports.import_runs")
                ).scalars()
            )
            rows = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_imports.import_run_rows")
                ).scalars()
            )
            partitions = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_imports.import_partitions")
                ).scalars()
            )
            assert runs == [tenant_a]
            assert rows == [tenant_a]
            assert partitions == [tenant_a]
            assert tenant_b not in runs
    finally:
        engine.dispose()


def test_online_role_without_tenant_context_sees_no_runs(
    migrated_scratch: tuple[str, str],
) -> None:
    """FORCE plus a GUC-testing predicate fails closed: no context, no rows."""
    admin_url, app_user_url = migrated_scratch
    _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_imports.import_runs")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_online_role_cannot_create_a_run_for_another_tenant(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                _insert_run(conn, tenant_b)
    finally:
        engine.dispose()


def test_two_workers_claim_different_partitions_without_waiting(
    migrated_scratch: tuple[str, str],
) -> None:
    """The second worker skips the first worker's uncommitted row lock."""
    from dotmac_imports.partitioning import claim_partition

    admin_url, app_user_url = migrated_scratch
    tenant_id = uuid.uuid4()
    admin = create_engine(admin_url)
    try:
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, 'claim-canary', 'Claim Canary')"
                ),
                {"id": tenant_id},
            )
            run_id = _insert_run(conn, tenant_id)
            conn.execute(
                text(
                    "UPDATE mod_imports.import_partitions SET status = 'pending' "
                    "WHERE tenant_id = :tenant AND run_id = :run"
                ),
                {"tenant": tenant_id, "run": run_id},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_imports.import_partitions ("
                    "id, tenant_id, run_id, ordinal, start_row, row_count, "
                    "partition_file_id, partition_checksum_sha256, byte_size, status"
                    ") VALUES ("
                    ":id, :tenant, :run, 1, 1, 1, :file, :digest, 64, 'pending'"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "run": run_id,
                    "file": uuid.uuid4(),
                    "digest": _DIGEST,
                },
            )
    finally:
        admin.dispose()

    engine = create_engine(app_user_url)
    first_connection = engine.connect()
    second_connection = engine.connect()
    first_transaction = first_connection.begin()
    second_transaction = second_connection.begin()
    try:
        first_connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        second_connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        with Session(first_connection) as first_session:
            first = claim_partition(first_session, tenant_id=tenant_id, run_id=run_id)
            with Session(second_connection) as second_session:
                second = claim_partition(
                    second_session, tenant_id=tenant_id, run_id=run_id
                )
                assert first is not None and second is not None
                assert {first.ordinal, second.ordinal} == {0, 1}
                assert first.partition_id != second.partition_id
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_connection.close()
        first_connection.close()
        engine.dispose()


def test_a_validated_run_can_be_promoted_only_once(
    migrated_scratch: tuple[str, str],
) -> None:
    """The database, not the orchestrator, is what decides this. Two operators
    pressing Apply together race, and `uq_import_runs_tenant_source_run` is what
    makes the loser lose."""
    admin_url, _ = migrated_scratch
    tenant_a, _tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            source_run = conn.execute(
                text(
                    "SELECT id FROM mod_imports.import_runs "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_a},
            ).scalar_one()
            for _ in range(2):
                conn.execute(
                    text(
                        "INSERT INTO mod_imports.import_runs ("
                        "id, tenant_id, kind, status, dry_run, source_run_id, "
                        "source_file_id, source_checksum_sha256, source_layout, "
                        "source_delimiter, source_encoding"
                        ") VALUES ("
                        ":id, :tenant, 'receipts', 'pending', false, :source, "
                        ":file, :digest, 'csv', ',', 'utf-8'"
                        ")"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_a,
                        "source": source_run,
                        "file": uuid.uuid4(),
                        "digest": _DIGEST,
                    },
                )
    except DBAPIError as exc:
        assert "uq_import_runs_tenant_source_run" in str(exc)
    else:
        pytest.fail("a validated run was promoted twice")
    finally:
        engine.dispose()


def test_postgres_validation_resumes_from_a_committed_running_checkpoint(
    migrated_scratch: tuple[str, str],
) -> None:
    """The unit suite proves the state machine; this canary proves the same
    checkpoint contract through PostgreSQL RLS and a fresh worker session."""
    admin_url, app_user_url = migrated_scratch
    tenant_a, _tenant_b = _seed_two_tenants(admin_url)
    data = b"Ref No,Amount Paid\nR-1,1\nR-2,2\nR-3,3\n"
    source = SourceDocument(
        file_id=uuid.uuid4(), checksum_sha256=hashlib.sha256(data).hexdigest()
    )
    fields = FieldSet(
        (
            FieldSpec("reference", required=True, aliases=frozenset({"Ref No"})),
            FieldSpec("amount", required=True, aliases=frozenset({"Amount Paid"})),
        )
    )
    mapping = ColumnMapping((("amount", "Amount Paid"), ("reference", "Ref No")))
    engine = create_engine(app_user_url)
    try:
        with Session(engine) as first_worker:
            first_worker.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            run = create_dry_run(
                first_worker,
                tenant_id=tenant_a,
                kind="receipts",
                source=source,
                mapping=mapping,
            )
            run_id = run.id
            first_worker.commit()
            first = validate_next_chunk(
                first_worker,
                tenant_id=tenant_a,
                run_id=run_id,
                data=data,
                fields=fields,
                validator=_AlwaysValid(),
                chunk_size=2,
            )
            assert (first.processed, first.status) == (2, RunStatus.RUNNING)
            first_worker.commit()

        with Session(engine) as resumed_worker:
            resumed_worker.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            final = validate_next_chunk(
                resumed_worker,
                tenant_id=tenant_a,
                run_id=run_id,
                data=data,
                fields=fields,
                validator=_AlwaysValid(),
                chunk_size=2,
            )
            assert (final.processed, final.status) == (
                3,
                RunStatus.DRY_RUN_READY,
            )
            resumed_worker.commit()

            replay = validate_next_chunk(
                resumed_worker,
                tenant_id=tenant_a,
                run_id=run_id,
                data=data,
                fields=fields,
                validator=_AlwaysValid(),
                chunk_size=2,
            )
            assert replay.is_complete
            assert (
                resumed_worker.query(ImportRunRow)
                .filter(ImportRunRow.run_id == run_id)
                .count()
                == 3
            )
    finally:
        engine.dispose()


# -- partition-manifest loader (dotmac-imports 0.2.0a2) ----------------------

_MANIFEST_DATA = b"Ref No,Amount Paid\nR-1,1\nR-2,2\nR-3,3\n"


def _manifest_fixture(
    admin_url: str,
) -> tuple[uuid.UUID, SourceDocument, dict[uuid.UUID, bytes], bytes]:
    import io

    from dotmac_imports import (
        PartitionDescriptor,
        iter_csv_partitions,
        render_partition_manifest,
    )

    tenant_id = uuid.uuid4()
    admin = create_engine(admin_url)
    try:
        with admin.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, 'Manifest')"
                ),
                {"id": tenant_id, "slug": f"mf-{tenant_id.hex[:10]}"},
            )
    finally:
        admin.dispose()
    source = SourceDocument(
        file_id=uuid.uuid4(),
        checksum_sha256=hashlib.sha256(_MANIFEST_DATA).hexdigest(),
    )
    stored: dict[uuid.UUID, bytes] = {}
    descriptors = []
    for payload in iter_csv_partitions(
        source, open_source=lambda: io.BytesIO(_MANIFEST_DATA), partition_rows=1
    ):
        file_id = uuid.uuid4()
        stored[file_id] = payload.data
        descriptors.append(
            PartitionDescriptor(
                ordinal=payload.ordinal,
                start_row=payload.start_row,
                row_count=payload.row_count,
                file_id=file_id,
                checksum_sha256=payload.checksum_sha256,
                byte_size=len(payload.data),
            )
        )
    return tenant_id, source, stored, render_partition_manifest(source, descriptors)


def _bind(session: Session, tenant_id: uuid.UUID) -> None:
    """Bind the tenant for every transaction this session begins."""
    from sqlalchemy import event

    @event.listens_for(session, "after_begin")
    def _set_tenant(_session, _transaction, connection):  # type: ignore[no-untyped-def]
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )


def test_postgres_concurrent_manifest_registration_is_idempotent(
    migrated_scratch: tuple[str, str],
) -> None:
    """Two loaders delivering one manifest: the run row lock serialises them,
    the loser sees the winner's plan and returns it; exactly one plan exists."""
    import threading

    from dotmac_imports import parse_partition_manifest, register_manifest_plan

    admin_url, app_user_url = migrated_scratch
    tenant_id, source, _stored, rendered = _manifest_fixture(admin_url)
    manifest = parse_partition_manifest(rendered)
    engine = create_engine(app_user_url)
    try:
        with Session(engine) as setup:
            _bind(setup, tenant_id)
            run = create_dry_run(
                setup,
                tenant_id=tenant_id,
                kind="opening",
                source=source,
                mapping=ColumnMapping(
                    (("amount", "Amount Paid"), ("reference", "Ref No"))
                ),
            )
            run_id = run.id
            setup.commit()

        winner = Session(engine)
        _bind(winner, tenant_id)
        first = register_manifest_plan(
            winner, tenant_id=tenant_id, run_id=run_id, source=source, manifest=manifest
        )
        outcome: dict[str, object] = {}

        def loser() -> None:
            with Session(engine) as session:
                _bind(session, tenant_id)
                rows = register_manifest_plan(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    source=source,
                    manifest=manifest,
                )
                outcome["ids"] = [row.id for row in rows]
                session.commit()

        thread = threading.Thread(target=loser)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "second loader should wait on the run row lock"
        winner.commit()
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert outcome["ids"] == [row.id for row in first]
        winner.close()

        with Session(engine) as check:
            _bind(check, tenant_id)
            count = check.execute(
                text(
                    "SELECT count(*) FROM mod_imports.import_partitions "
                    "WHERE run_id = :run"
                ),
                {"run": run_id},
            ).scalar_one()
            assert count == 3
    finally:
        engine.dispose()


def test_postgres_manifest_plan_is_immutable_and_drives_claims_and_apply(
    migrated_scratch: tuple[str, str],
) -> None:
    import io

    from dotmac_imports import (
        PartitionPlanInvalid,
        apply_claimed_partition,
        claim_partition,
        parse_partition_manifest,
        read_claimed_partition,
        register_manifest_plan,
        validate_claimed_partition,
        verify_manifest_partitions,
    )

    class _Applier:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def apply(self, row: Mapping[str, str]) -> Mapping[str, object]:
            self.seen.append(row["reference"])
            return {"reference": row["reference"]}

    admin_url, app_user_url = migrated_scratch
    tenant_id, source, stored, rendered = _manifest_fixture(admin_url)
    manifest = parse_partition_manifest(rendered)
    verify_manifest_partitions(
        manifest, open_partition=lambda file_id: io.BytesIO(stored[file_id])
    )
    fields = FieldSet(
        (
            FieldSpec("reference", required=True, aliases=frozenset({"Ref No"})),
            FieldSpec("amount", required=True, aliases=frozenset({"Amount Paid"})),
        )
    )
    engine = create_engine(app_user_url)
    opener = lambda file_id: io.BytesIO(stored[file_id])  # noqa: E731
    try:
        with Session(engine) as session:
            _bind(session, tenant_id)
            run = create_dry_run(
                session,
                tenant_id=tenant_id,
                kind="opening",
                source=source,
                mapping=ColumnMapping(
                    (("amount", "Amount Paid"), ("reference", "Ref No"))
                ),
            )
            run_id = run.id
            register_manifest_plan(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                source=source,
                manifest=manifest,
            )
            session.commit()
            # A different (re-partitioned) plan for the same run is refused.
            with pytest.raises(PartitionPlanInvalid):
                register_manifest_plan(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    source=source,
                    manifest=parse_partition_manifest(
                        _resplit(source, manifest.partitions)
                    ),
                )
            session.rollback()

            # Two workers lease distinct manifest partitions without waiting.
            with Session(engine) as other_worker:
                _bind(other_worker, tenant_id)
                first = claim_partition(session, tenant_id=tenant_id, run_id=run_id)
                second = claim_partition(
                    other_worker, tenant_id=tenant_id, run_id=run_id
                )
                assert first is not None and second is not None
                assert {first.ordinal, second.ordinal} == {0, 1}
                for claim, worker in ((first, session), (second, other_worker)):
                    validate_claimed_partition(
                        worker,
                        read_claimed_partition(claim, open_partition=opener),
                        fields=fields,
                        validator=_AlwaysValid(),
                    )
                    worker.commit()
            while (
                claim := claim_partition(session, tenant_id=tenant_id, run_id=run_id)
            ) is not None:
                validate_claimed_partition(
                    session,
                    read_claimed_partition(claim, open_partition=opener),
                    fields=fields,
                    validator=_AlwaysValid(),
                )
                session.commit()
            assert get_run(session, tenant_id=tenant_id, run_id=run_id).status == (
                RunStatus.DRY_RUN_READY
            )

            applied = promote(
                session, tenant_id=tenant_id, run_id=run_id, source=source
            )
            session.commit()
            applier = _Applier()
            while (
                claim := claim_partition(
                    session, tenant_id=tenant_id, run_id=applied.id
                )
            ) is not None:
                apply_claimed_partition(
                    session,
                    read_claimed_partition(claim, open_partition=opener),
                    fields=fields,
                    validator=_AlwaysValid(),
                    applier=applier,
                )
                session.commit()
            assert sorted(applier.seen) == ["R-1", "R-2", "R-3"]
            assert get_run(session, tenant_id=tenant_id, run_id=applied.id).status == (
                RunStatus.COMPLETED
            )
    finally:
        engine.dispose()


def _resplit(source, partitions):  # type: ignore[no-untyped-def]
    """A same-source plan with one fewer partition (a different plan)."""
    from dotmac_imports import PartitionDescriptor, render_partition_manifest

    first, second = partitions[0], partitions[1]
    merged = PartitionDescriptor(
        ordinal=0,
        start_row=0,
        row_count=first.row_count + second.row_count,
        file_id=first.file_id,
        checksum_sha256=first.checksum_sha256,
        byte_size=first.byte_size,
    )
    return render_partition_manifest(source, (merged, *partitions[2:]))
