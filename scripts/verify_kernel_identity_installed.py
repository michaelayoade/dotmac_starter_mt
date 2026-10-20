"""Hosted candidate-only public identity proof, copied outside the checkout.

No test modules, checkout imports, private verifier, target-derived expectations,
stamped revisions, material diagnostics or release/adoption authority.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import site
import sys
import tempfile
import zipfile
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def installed_origins() -> dict[str, str]:
    roots = tuple(Path(p).resolve() for p in site.getsitepackages())
    prefix = Path(sys.prefix).resolve()
    require(sys.prefix != sys.base_prefix, "An isolated venv is required")
    require(all(p.is_relative_to(prefix) for p in roots), "Foreign site-packages")
    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name != "dotmac_kernel" and not name.startswith("dotmac_kernel."):
            continue
        origin = getattr(module, "__file__", None)
        require(origin is not None, "Kernel module has no file origin")
        path = Path(origin).resolve(strict=True)
        require(
            any(path.is_relative_to(root) for root in roots),
            "Kernel checkout origin refused",
        )
        origins[name] = str(path)
    return origins


def verify_package(receipt: dict[str, object]):
    import sqlalchemy as sa
    import sqlalchemy.engine.create as engine_create
    import sqlalchemy.orm as orm

    def refused(*args, **kwargs):
        raise RuntimeError("Public import attempted engine/session creation")

    original_create = sa.create_engine
    original_engine_create = engine_create.create_engine
    original_session_init = orm.Session.__init__
    original_maker_call = orm.sessionmaker.__call__
    sa.create_engine = engine_create.create_engine = refused
    orm.Session.__init__ = refused
    orm.sessionmaker.__call__ = refused
    try:
        import dotmac_kernel
        from dotmac_kernel import credential_lifecycle
        from dotmac_kernel.public_exports import load_public_exports

        catalogue = load_public_exports()
        require(
            set(catalogue.root_exports) == set(dotmac_kernel.__all__),
            "Root catalogue differs",
        )
        require(
            catalogue.supported_modules == dotmac_kernel.SUPPORTED_MODULES,
            "Supported catalogue differs",
        )
        require(
            catalogue.internal_modules == dotmac_kernel.INTERNAL_MODULES,
            "Internal catalogue differs",
        )
        for name in (
            "dotmac_kernel.identity_storage",
            "dotmac_kernel.identity_provisioning",
            "dotmac_kernel.migrations.identity_history",
            "dotmac_kernel.credential_lifecycle",
        ):
            module = importlib.import_module(name)
            exports = catalogue.exports_for(name)
            require(set(exports) == set(module.__all__), "Module catalogue differs")
            for export in exports:
                getattr(module, export)
        require(
            "verify_identity_history"
            in catalogue.exports_for("dotmac_kernel.migrations.identity_history"),
            "Public verifier missing",
        )
        for name in (
            "CredentialAccountRef",
            "CredentialAccountRecoveryAuthorizationV1",
            "CredentialAccountResetPort",
            "CredentialCreateAbsentV1",
            "CredentialCreateAbsentFromProofV1",
            "CredentialReplaceSelectedV1",
            "CredentialAuthenticatedAccountPasswordChangeV1",
            "CredentialAuthenticatedAccountPasswordChangePort",
        ):
            require(
                name in credential_lifecycle.__all__,
                "Credential public command missing",
            )
            getattr(credential_lifecycle, name)
        for name in (
            "assign_authorized_password",
            "change_authenticated_account_password",
            "complete_account_reset",
        ):
            require(
                callable(getattr(credential_lifecycle.CredentialLifecycle, name)),
                "Credential method missing",
            )
    finally:
        sa.create_engine = original_create
        engine_create.create_engine = original_engine_create
        orm.Session.__init__ = original_session_init
        orm.sessionmaker.__call__ = original_maker_call

    version = importlib.metadata.version("dotmac-kernel")
    require(
        version == dotmac_kernel.__version__ == "0.1.0a106+dev",
        "Candidate development version differs",
    )
    wheel = Path(os.environ["IDENTITY_WHEEL_PATH"]).resolve(strict=True)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    require(
        digest == os.environ["IDENTITY_WHEEL_SHA256"], "Candidate wheel digest differs"
    )
    root = Path(dotmac_kernel.__file__).resolve().parent
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        ]
        require(len(metadata_paths) == 1, "Wheel metadata ambiguous")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        require(
            metadata["Name"] == "dotmac-kernel" and metadata["Version"] == version,
            "Wheel identity differs",
        )
        for name in archive.namelist():
            if name.startswith("dotmac_kernel/") and not name.endswith("/"):
                path = root / name.removeprefix("dotmac_kernel/")
                require(
                    path.is_file() and path.read_bytes() == archive.read(name),
                    "Installed wheel file differs",
                )
    receipt.update(
        installed_version=version,
        wheel_sha256=digest,
        catalogue_sha256=catalogue.digest,
        origins=installed_origins(),
        import_without_engine_or_session="passed",
        installed_wheel_bytes="passed",
    )
    return sa


def exercise_public_history(sa, receipt: dict[str, object]) -> None:
    from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
    from dotmac_kernel.migrations import versions_dir
    from dotmac_kernel.migrations.identity_history import (
        HistoricalAuditEventV1,
        HistoricalCredentialV1,
        HistoricalPersonRoleV1,
        HistoricalPersonV1,
        HistoricalRoleV1,
        HistoricalSessionV1,
        HistoricalTenantDomainV1,
        HistoricalTenantV1,
        IdentityHistoryCohortV1,
        IdentityHistoryImportOperationV1,
        IdentityHistoryImportRefused,
        IdentityHistoryManifestV1,
        IdentityHistoryRelationsV1,
        IdentityHistoryRowsV1,
        import_identity_history,
        verify_identity_history,
    )
    from sqlalchemy.engine import make_url

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    require(
        re.fullmatch(r"[0-9]+", run) and re.fullmatch(r"[0-9]+", attempt),
        "Hosted run identity required",
    )
    url = make_url(os.environ["IDENTITY_INSTALLED_DATABASE_URL"])
    require(
        url.database
        != make_url(os.environ["IDENTITY_REFERENCE_DATABASE_URL"]).database,
        "Reference database reused",
    )
    require(
        url.drivername == "postgresql+psycopg"
        and url.host == "localhost"
        and url.port == 5433
        and url.username == "app_admin"
        and url.password is None
        and url.database == f"starter_identity_installed_{run}_{attempt}"
        and not url.query,
        "Dedicated loopback app_admin probe database required",
    )
    require(url.database != os.environ["TEST_DB_NAME"], "Reference database refused")
    require(
        url.database != make_url(os.environ["IDENTITY_GRAPH_DATABASE_URL"]).database,
        "Graph database refused",
    )
    install_audit_actions(AuditActionRegistry.from_manifests(()))
    engine = sa.create_engine(url, hide_parameters=True, echo=False)
    revisions: list[dict[str, str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="installed-migrations-") as directory:
            env = Path(directory)
            (env / "env.py").write_text(
                "from alembic import context\n"
                "config=context.config\n"
                "context.configure(connection=config.attributes['connection'],"
                "target_metadata=None,version_table_schema='public',transactional_dd"
                "l=True,"
                "on_version_apply=config.attributes['on_version_apply'])\n"
                "with context.begin_transaction():\n    context.run_migrations()\n"
            )
            cfg = Config()
            cfg.set_main_option("script_location", str(env))
            cfg.set_main_option("version_locations", str(versions_dir()))
            scripts = ScriptDirectory.from_config(cfg)
            require(
                scripts.get_heads() == ["0030_credential_tenant_key"],
                "Installed migration head differs",
            )

            def observe(*, ctx, step, heads, run_args):
                for revision in step.up_revision_ids:
                    path = Path(scripts.get_revision(revision).path).resolve(
                        strict=True
                    )
                    require(
                        path.is_relative_to(Path(sys.prefix).resolve()),
                        "Foreign migration source",
                    )
                    revisions.append(
                        {
                            "revision": revision,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )

            cfg.attributes["on_version_apply"] = observe
            with engine.begin() as connection:
                role = connection.execute(
                    sa.text(
                        "SELECT current_user, session_user, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                ).one()
                require(
                    tuple(role) == ("app_admin", "app_admin", False, True),
                    "Real offline role required",
                )
                require(
                    connection.scalar(
                        sa.text(
                            "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid=c.relnamespace WHERE n.nspname NOT IN "
                            "('pg_catalog','information_schema') "
                            "AND n.nspname NOT LIKE 'pg_toast%' "
                            "AND c.relkind IN ('r','p','v','m','S','f')"
                        )
                    )
                    == 0,
                    "Probe database not empty",
                )
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "0030_credential_tenant_key")
            require(
                {r["revision"] for r in revisions}
                == {r.revision for r in scripts.walk_revisions()},
                "Genuine replay incomplete",
            )
            require(
                len(revisions) == len({r["revision"] for r in revisions}),
                "Duplicate migration observation",
            )
        receipt["migration_revisions"] = revisions

        # Independent pre-write original rows. These never derive from targets.
        now = datetime.now(UTC) - timedelta(days=1)
        timed = {"created_at": now, "updated_at": now + timedelta(seconds=1)}
        tenant, person, role = uuid4(), uuid4(), uuid4()
        synthetic_marker = "synthetic-stored-history-only"
        rows = IdentityHistoryRowsV1(
            tenants=(
                HistoricalTenantV1(
                    id=tenant,
                    slug="synthetic-installed",
                    name="Synthetic installed tenant",
                    is_active=True,
                    suspended_at=None,
                    deleted_at=None,
                    **timed,
                ),
            ),
            tenant_domains=(
                HistoricalTenantDomainV1(
                    id=uuid4(),
                    tenant_id=tenant,
                    domain="installed.example.test",
                    verified_at=now,
                    **timed,
                ),
            ),
            people=(
                HistoricalPersonV1(
                    id=person,
                    tenant_id=tenant,
                    email="installed@example.test",
                    first_name="Installed",
                    last_name="Probe",
                    **timed,
                ),
            ),
            user_credentials=(
                HistoricalCredentialV1(
                    id=uuid4(),
                    tenant_id=tenant,
                    person_id=person,
                    password_hash=synthetic_marker,
                    **timed,
                ),
            ),
            auth_sessions=(
                HistoricalSessionV1(
                    id=uuid4(),
                    tenant_id=tenant,
                    person_id=person,
                    token_hash="0" * 64,
                    expires_at=now + timedelta(hours=1),
                    revoked_at=now,
                    **timed,
                ),
            ),
            roles=(
                HistoricalRoleV1(
                    id=role,
                    tenant_id=tenant,
                    slug="synthetic",
                    name="Synthetic",
                    **timed,
                ),
            ),
            person_roles=(
                HistoricalPersonRoleV1(
                    id=uuid4(),
                    tenant_id=tenant,
                    person_id=person,
                    role_id=role,
                    **timed,
                ),
            ),
            audit_events=(
                HistoricalAuditEventV1(
                    id=uuid4(),
                    tenant_id=tenant,
                    actor_person_id=person,
                    action="synthetic.history",
                    entity_type="person",
                    entity_id="legacy-person:7",
                    details={"synthetic": [None, False, 0]},
                    created_at=now,
                ),
            ),
        )
        families = (
            ("tenants", HistoricalTenantV1),
            ("tenant_domains", HistoricalTenantDomainV1),
            ("people", HistoricalPersonV1),
            ("user_credentials", HistoricalCredentialV1),
            ("auth_sessions", HistoricalSessionV1),
            ("roles", HistoricalRoleV1),
            ("person_roles", HistoricalPersonRoleV1),
            ("audit_events", HistoricalAuditEventV1),
        )
        schema = "installed_identity_source"
        metadata = sa.MetaData(schema=schema)
        tables = {}
        for family, row_type in families:
            field_types = {
                **dict.fromkeys(
                    ("id", "tenant_id", "person_id", "role_id", "actor_person_id"),
                    sa.Uuid,
                ),
                **dict.fromkeys(
                    (
                        "created_at",
                        "updated_at",
                        "suspended_at",
                        "deleted_at",
                        "verified_at",
                        "expires_at",
                        "revoked_at",
                    ),
                    lambda: sa.DateTime(timezone=True),
                ),
                "is_active": sa.Boolean,
                "details": sa.JSON,
                **dict.fromkeys(
                    (
                        "slug",
                        "name",
                        "domain",
                        "email",
                        "first_name",
                        "last_name",
                        "password_hash",
                        "token_hash",
                        "action",
                        "entity_type",
                        "entity_id",
                    ),
                    sa.Text,
                ),
            }
            require(
                {f.name for f in fields(row_type)} <= field_types.keys(),
                "Unmapped historical field",
            )
            columns = []
            for field in fields(row_type):
                kind = field_types[field.name]()
                columns.append(
                    sa.Column(
                        field.name,
                        kind,
                        primary_key=field.name == "id",
                        nullable=field.name
                        in {
                            "actor_person_id",
                            "revoked_at",
                            "verified_at",
                            "suspended_at",
                            "deleted_at",
                            "entity_id",
                        },
                    )
                )
            tables[family] = sa.Table(family, metadata, *columns)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    'CREATE SCHEMA "installed_identity_source" AUTHORIZATION app_admin'
                )
            )
            # Synthetic source only; installed migrations above created the targets.
            metadata.create_all(connection)
            for family, _ in families:
                for row in getattr(rows, family):
                    connection.execute(sa.insert(tables[family]).values(**asdict(row)))
            oids = {
                family: connection.scalar(
                    sa.text(
                        "SELECT c.oid FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname=:schema "
                        "AND c.relname=:name "
                        "AND pg_get_userbyid(c.relowner)='app_admin'"
                    ),
                    {"schema": schema, "name": family},
                )
                for family, _ in families
            }
        cohort = IdentityHistoryCohortV1(
            **{
                family: frozenset(row.id for row in getattr(rows, family))
                for family, _ in families
            }
        )
        expected = IdentityHistoryManifestV1(
            relations=IdentityHistoryRelationsV1(schema=schema, **oids),
            cohort=cohort,
            source_revision=os.environ["IDENTITY_SOURCE_SHA"],
            session_issuance_review_ref="synthetic-revoked-session-not-admission",
            password_session_ids=cohort.auth_sessions,
            installed_kernel_head="0030_credential_tenant_key",
        )
        operation = IdentityHistoryImportOperationV1(
            is_active=True,
            activated_person_ids=cohort.people,
            reason="Synthetic installed verification",
            decision_ref="hosted-installed-probe",
            actor_type="service",
            actor_id="hosted-installed-probe",
            source_application="dotmac_starter_mt",
        )

        class SyntheticSource:
            def decode(self, connection):
                return IdentityHistoryRowsV1(
                    **{
                        family: tuple(
                            row_type(**dict(row))
                            for row in connection.execute(
                                sa.select(tables[family])
                            ).mappings()
                        )
                        for family, row_type in families
                    }
                )

        targets = (
            "tenants",
            "tenant_domains",
            "parties",
            "party_persons",
            "user_credentials",
            "auth_sessions",
            "roles",
            "party_role_grants",
            "audit_events",
        )

        def snapshot(connection, observed):
            # Mutation observer only; verifier expectations stay pre-write rows.
            result = []
            for table in observed:
                expression = sa.cast(
                    sa.func.row_to_json(table.table_valued()), sa.Text()
                )
                result.append(
                    tuple(
                        connection.scalars(sa.select(expression).order_by(expression))
                    )
                )
            return tuple(result)

        def writes(connection):
            return tuple(
                connection.execute(
                    sa.text(
                        "SELECT relid, n_tup_ins, n_tup_upd, n_tup_del "
                        "FROM pg_stat_xact_user_tables "
                        "WHERE schemaname='public' ORDER BY relid"
                    )
                )
            )

        with engine.connect() as connection:
            root = connection.begin()
            try:
                observed = tuple(
                    sa.Table(
                        name, sa.MetaData(), schema="public", autoload_with=connection
                    )
                    for name in targets
                )
                imported = import_identity_history(
                    connection, SyntheticSource(), expected, operation
                )
                require(
                    imported.counts
                    == type(imported.counts)(
                        **{family: len(getattr(rows, family)) for family, _ in families}
                    ),
                    "Importer count receipt differs",
                )
                require(
                    len(imported.current_audit_ids) == 1,
                    "Actual current import audit missing",
                )
                before, before_writes = (
                    snapshot(connection, observed),
                    writes(connection),
                )
                verify_identity_history(connection, rows, expected, operation, imported)
                require(
                    connection.get_transaction() is root and root.is_active,
                    "Verifier changed caller root",
                )
                require(
                    snapshot(connection, observed) == before
                    and writes(connection) == before_writes,
                    "Positive verification wrote targets or audit",
                )
                entity_column = next(
                    column
                    for column in sa.inspect(connection).get_columns(
                        "audit_events", schema=schema
                    )
                    if column["name"] == "entity_id"
                )
                require(
                    isinstance(entity_column["type"], sa.String),
                    "Historical entity_id must be text",
                )
                decoded = SyntheticSource().decode(connection)
                require(bool(decoded == rows), "Independent source roundtrip differs")
                require(
                    all(
                        type(row.entity_id) is str or row.entity_id is None
                        for row in decoded.audit_events
                    ),
                    "Historical entity_id shape differs",
                )
                require(
                    not connection.in_nested_transaction(),
                    "Text sensitivity must use the caller root",
                )
                connection.execute(
                    sa.text(
                        "UPDATE public.audit_events SET entity_id='changed-text' WHERE "
                        "id=:id"
                    ),
                    {"id": rows.audit_events[0].id},
                )
                text_before, text_writes = (
                    snapshot(connection, observed),
                    writes(connection),
                )
                try:
                    verify_identity_history(
                        connection, rows, expected, operation, imported
                    )
                except IdentityHistoryImportRefused:
                    pass
                else:
                    raise RuntimeError(
                        "Public verifier accepted historical text tamper"
                    )
                require(
                    snapshot(connection, observed) == text_before
                    and writes(connection) == text_writes,
                    "Text refusal wrote targets or audit",
                )
                require(
                    connection.get_transaction() is root and root.is_active,
                    "Text refusal changed caller root",
                )
                connection.execute(
                    sa.text(
                        "UPDATE public.audit_events SET entity_id=:entity_id "
                        "WHERE id=:id"
                    ),
                    {
                        "entity_id": rows.audit_events[0].entity_id,
                        "id": rows.audit_events[0].id,
                    },
                )
                restored, restored_writes = (
                    snapshot(connection, observed),
                    writes(connection),
                )
                require(restored == before, "Independent text restoration differs")
                verify_identity_history(connection, rows, expected, operation, imported)
                require(
                    snapshot(connection, observed) == restored
                    and writes(connection) == restored_writes,
                    "Restored positive verification wrote targets or audit",
                )
                require(
                    connection.get_transaction() is root
                    and root.is_active
                    and not connection.in_nested_transaction(),
                    "Restored positive verification changed caller root",
                )
                connection.execute(
                    sa.text("UPDATE public.tenants SET name=:name WHERE id=:id"),
                    {"name": "synthetic-wrong-value", "id": tenant},
                )
                tampered, tampered_writes = (
                    snapshot(connection, observed),
                    writes(connection),
                )
                try:
                    verify_identity_history(
                        connection, rows, expected, operation, imported
                    )
                except IdentityHistoryImportRefused:
                    pass
                else:
                    raise RuntimeError(
                        "Public verifier accepted a wrong persisted value"
                    )
                require(
                    connection.get_transaction() is root and root.is_active,
                    "Refusal changed caller root",
                )
                require(
                    snapshot(connection, observed) == tampered
                    and writes(connection) == tampered_writes,
                    "Refusal wrote targets or audit",
                )
            finally:
                root.rollback()
            require(
                all(
                    connection.scalar(sa.select(sa.func.count()).select_from(table))
                    == 0
                    for table in observed
                ),
                "Caller rollback did not remove imports",
            )
            connection.rollback()
        receipt.update(
            public_import="passed",
            public_verify_positive="passed",
            value_tamper_refusal="passed",
            entity_text_roundtrip="passed",
            entity_text_tamper_refusal="passed",
            verification_no_writes_or_audit="passed",
            caller_root_rollback="passed",
        )
    finally:
        engine.dispose()


def main() -> int:
    receipt: dict[str, object] = {
        "schema": "KernelInstalledIdentityCandidate.v1",
        "status": "failed",
        "authority": "candidate-only",
    }
    destination = Path(os.environ["IDENTITY_INSTALLED_RECEIPT"])
    try:
        require(
            os.environ.get("CI") == "true"
            and os.environ.get("GITHUB_ACTIONS") == "true",
            "Hosted CI required",
        )
        require("PYTHONPATH" not in os.environ, "PYTHONPATH must be unset")
        checkout = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        require(
            not Path.cwd().resolve().is_relative_to(checkout),
            "Outside-checkout cwd required",
        )
        require(
            not Path(__file__).resolve().is_relative_to(checkout),
            "Outside-checkout probe required",
        )
        require(
            all(not Path(p).resolve().is_relative_to(checkout) for p in sys.path if p),
            "Checkout on sys.path refused",
        )
        require(
            re.fullmatch(r"[0-9a-f]{40}", os.environ["IDENTITY_SOURCE_SHA"]),
            "Exact source SHA required",
        )
        receipt["source_sha"] = os.environ["IDENTITY_SOURCE_SHA"]
        sa = verify_package(receipt)
        exercise_public_history(sa, receipt)
        receipt["origins"] = installed_origins()
        receipt["status"] = "passed"
        return 0
    except Exception:
        # Driver diagnostics can contain material. Only a closed failure status leaves.
        print(
            "Installed identity candidate qualification failed; see material-free st"
            "atus receipt",
            file=sys.stderr,
        )
        return 1
    finally:
        destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
