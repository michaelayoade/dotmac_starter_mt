"""Hosted PostgreSQL proof of the genuinely migrated credential FK target.

Consumes the already-migrated reference admin_engine; never self-migrates or
creates canonical tables from ORM metadata. Normal unchanged Alembic replay and
hosted per-revision observations remain required evidence beyond head/catalog.
Only rollback-owned synthetic fixtures and a disposable FK probe are written.
The sentinel is non-authenticating: no credential continuity claim, password
primitive, existing credential update/delete or session write occurs here.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotmac_kernel.migrations import versions_dir
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant, UserCredential
from sqlalchemy.exc import DBAPIError

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

_KEY = "uq_user_credentials_tenant_id_id"
_REVISION = "0030_credential_tenant_key"
_SENTINEL = "synthetic-non-authenticating-credential"


def _qualified(model):
    return model.__table__.to_metadata(sa.MetaData(), schema="public")


def _canonical_key(connection):
    return connection.execute(
        sa.text(
            "SELECT c.convalidated, c.condeferrable, i.indisunique, i.indisvalid, "
            "i.indisready, i.indislive, i.indpred IS NULL, i.indexprs IS NULL, "
            "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY "
            "k(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid=c.conrelid AND "
            "a.attnum=k.attnum ORDER BY k.ord) "
            "FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid "
            "WHERE c.conrelid='public.user_credentials'::regclass "
            "AND c.conname=:name AND c.contype='u'"
        ),
        {"name": _KEY},
    ).one_or_none()


def _assert_installed_key(connection):
    scripts = ScriptDirectory(
        str(versions_dir().parent), version_locations=[str(versions_dir())]
    )
    heads = scripts.get_heads()
    assert len(heads) == 1
    assert _REVISION in {script.revision for script in scripts.walk_revisions()}
    assert scripts.get_revision(_REVISION).down_revision == "0029_audit_history_shape"
    live = set(
        connection.scalars(sa.text("SELECT version_num FROM public.alembic_version"))
    )
    # alembic_version records branch HEADS, not applied revision history.
    assert heads[0] in live
    facts = _canonical_key(connection)
    assert facts is not None
    assert tuple(facts[:8]) == (True, False, True, True, True, True, True, True)
    assert list(facts[8]) == ["tenant_id", "id"]
    inspector = sa.inspect(connection)
    assert inspector.get_pk_constraint("user_credentials", schema="public")[
        "constrained_columns"
    ] == ["id"]
    declared = {
        tuple(column.name for column in constraint.columns)
        for constraint in UserCredential.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("tenant_id", "id") in declared


@pytest.fixture
def credential_fk_case(admin_engine):
    with admin_engine.connect() as connection:
        root = connection.begin()
        try:
            _assert_installed_key(connection)
            role = connection.execute(
                sa.text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM "
                    "pg_roles WHERE rolname=current_user"
                )
            ).one()
            assert tuple(role) == ("app_admin", False, True)
            credential_oid = connection.scalar(
                sa.text("SELECT 'public.user_credentials'::regclass::oid")
            )
            tenants = (uuid4(), uuid4())
            parties = (uuid4(), uuid4())
            credential_id = uuid4()
            now = datetime.now(UTC)
            for tenant, party in zip(tenants, parties, strict=True):
                connection.execute(
                    sa.insert(_qualified(Tenant)).values(
                        id=tenant,
                        slug="credential-key-" + str(tenant),
                        name="Synthetic FK tenant",
                        is_active=True,
                        suspended_at=None,
                        deleted_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    sa.insert(_qualified(Party)).values(
                        id=party,
                        tenant_id=tenant,
                        party_type=PartyType.person,
                        display_name="Synthetic Person",
                        email="synthetic-" + str(party) + "@example.test",
                        is_active=True,
                        custom_fields={},
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    sa.insert(_qualified(PartyPerson)).values(
                        party_id=party, first_name="Synthetic", last_name="Person"
                    )
                )
            connection.execute(
                sa.insert(_qualified(UserCredential)).values(
                    id=credential_id,
                    tenant_id=tenants[0],
                    party_id=parties[0],
                    password_hash=_SENTINEL,
                    created_at=now,
                    updated_at=now,
                )
            )
            schema = "credential_fk_probe_" + uuid4().hex
            connection.execute(
                sa.text(f'CREATE SCHEMA "{schema}" AUTHORIZATION app_admin')
            )
            probe = sa.Table(
                "credential_policy_probe",
                sa.MetaData(),
                sa.Column("tenant_id", sa.Uuid(), nullable=False),
                sa.Column("credential_id", sa.Uuid(), primary_key=True),
                schema=schema,
            )
            yield SimpleNamespace(
                connection=connection,
                root=root,
                tenants=tenants,
                parties=parties,
                credential_id=credential_id,
                schema=schema,
                probe=probe,
                credential_oid=credential_oid,
                timestamp=now,
            )
        finally:
            if connection.in_transaction():
                connection.rollback()


def _create_probe(case):
    quote = case.connection.dialect.identifier_preparer.quote_identifier
    # Physical FK DDL targets the actual migrated Kernel table; no surrogate
    # credential table, redundant party column or ORM-only assertion.
    case.connection.execute(
        sa.text(
            f"CREATE TABLE {quote(case.schema)}.credential_policy_probe ("
            "tenant_id uuid NOT NULL, credential_id uuid PRIMARY KEY, "
            "CONSTRAINT credential_policy_probe_tenant_credential_fk "
            "FOREIGN KEY (tenant_id, credential_id) "
            "REFERENCES public.user_credentials (tenant_id, id) ON DELETE CASCADE)"
        )
    )


def _assert_synthetic_credential_unchanged(case):
    table = _qualified(UserCredential)
    row = (
        case.connection.execute(
            sa.select(table).where(table.c.id == case.credential_id)
        )
        .mappings()
        .one()
    )
    intact = (
        row["id"] == case.credential_id
        and row["tenant_id"] == case.tenants[0]
        and row["party_id"] == case.parties[0]
        and row["password_hash"] == _SENTINEL
        and row["created_at"] == case.timestamp
        and row["updated_at"] == case.timestamp
    )
    if not intact:
        pytest.fail("synthetic credential changed during FK probe", pytrace=False)
    assert (
        case.connection.scalar(
            sa.text("SELECT 'public.user_credentials'::regclass::oid")
        )
        == case.credential_oid
    )


def _rollback_and_assert_canonical_preserved(case):
    case.root.rollback()
    with case.connection.begin():
        _assert_installed_key(case.connection)
        assert (
            case.connection.scalar(
                sa.text("SELECT 'public.user_credentials'::regclass::oid")
            )
            == case.credential_oid
        )
        table = _qualified(UserCredential)
        assert (
            case.connection.scalar(
                sa.select(table.c.id).where(table.c.id == case.credential_id)
            )
            is None
        )
        assert (
            case.connection.scalar(
                sa.text("SELECT to_regclass(:probe)"),
                {"probe": case.schema + ".credential_policy_probe"},
            )
            is None
        )


def test_installed_credential_key_supports_tenant_fk_and_caller_rollback(
    credential_fk_case,
):
    case = credential_fk_case
    _create_probe(case)
    foreign_keys = sa.inspect(case.connection).get_foreign_keys(
        case.probe.name, schema=case.schema
    )
    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert fk["constrained_columns"] == ["tenant_id", "credential_id"]
    assert fk["referred_schema"] == "public"
    assert fk["referred_table"] == "user_credentials"
    assert fk["referred_columns"] == ["tenant_id", "id"]
    assert fk["options"]["ondelete"] == "CASCADE"
    assert {
        column["name"]
        for column in sa.inspect(case.connection).get_columns(
            case.probe.name, schema=case.schema
        )
    } == {"tenant_id", "credential_id"}
    for tenant, credential in (
        (case.tenants[1], case.credential_id),
        (case.tenants[0], uuid4()),
    ):
        savepoint = case.connection.begin_nested()
        with pytest.raises(DBAPIError) as rejected:
            case.connection.execute(
                sa.insert(case.probe).values(tenant_id=tenant, credential_id=credential)
            )
        assert rejected.value.orig.sqlstate == "23503"
        savepoint.rollback()
        _assert_synthetic_credential_unchanged(case)
    case.connection.execute(
        sa.insert(case.probe).values(
            tenant_id=case.tenants[0], credential_id=case.credential_id
        )
    )
    assert case.connection.execute(
        sa.select(case.probe.c.tenant_id, case.probe.c.credential_id)
    ).one() == (case.tenants[0], case.credential_id)
    _assert_synthetic_credential_unchanged(case)
    # The migration's ordinary DROP is RESTRICT: dependent product storage
    # cannot be silently removed by a Kernel downgrade.
    savepoint = case.connection.begin_nested()
    scripts = ScriptDirectory(
        str(versions_dir().parent), version_locations=[str(versions_dir())]
    )
    revision = scripts.get_revision(_REVISION)
    with pytest.raises(DBAPIError) as dependent:
        with Operations.context(MigrationContext.configure(case.connection)):
            revision.module.downgrade()
    assert dependent.value.orig.sqlstate == "2BP01"
    savepoint.rollback()
    assert _canonical_key(case.connection) is not None
    _assert_synthetic_credential_unchanged(case)
    _rollback_and_assert_canonical_preserved(case)


@pytest.mark.parametrize("replacement", ["missing", "partial_false"])
def test_old_key_effect_and_partial_substitute_cannot_admit_tenant_fk(
    credential_fk_case, replacement
):
    case = credential_fk_case
    # Simulate only the missing 0029 key effect inside a rollback transaction;
    # never stamp/change alembic_version or rewrite/replay a published revision.
    case.connection.execute(
        sa.text(
            "ALTER TABLE public.user_credentials DROP CONSTRAINT "
            "uq_user_credentials_tenant_id_id"
        )
    )
    if replacement == "partial_false":
        case.connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_user_credentials_tenant_id_id ON "
                "public.user_credentials (tenant_id, id) WHERE false"
            )
        )
    assert _canonical_key(case.connection) is None
    savepoint = case.connection.begin_nested()
    with pytest.raises(DBAPIError) as rejected:
        _create_probe(case)
    assert rejected.value.orig.sqlstate == "42830"
    savepoint.rollback()
    _assert_synthetic_credential_unchanged(case)
    assert (
        case.connection.scalar(
            sa.text("SELECT to_regclass(:probe)"),
            {"probe": case.schema + ".credential_policy_probe"},
        )
        is None
    )
    _rollback_and_assert_canonical_preserved(case)
