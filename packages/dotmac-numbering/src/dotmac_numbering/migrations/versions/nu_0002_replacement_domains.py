"""Add immutable creation/enrollment and exact claims across replacement codes.

Released nu_0001 bytes are unchanged. Both persistence planes are selected
explicitly; a platform-only installation never needs the tenant catalogue.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "nu_0002_replacement_domains"
down_revision = "nu_0001_numbering"
branch_labels = None
MODULE_CODE = "numbering"
COMMON_REQUIRES = ("module_database_roles.v1", "idempotency_ledger.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES
depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)
_SCHEMA = "mod_numbering"


def _domain_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("domain_code", sa.String(80), nullable=False),
        sa.Column("partitioned", sa.Boolean(), nullable=False),
    ]


def _claim_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("domain_code", sa.String(80), nullable=False),
        sa.Column("partition_key", sa.String(128, collation="C"), nullable=False),
        sa.Column("formatted_number", sa.String(255, collation="C"), nullable=False),
        sa.Column("document_reference", sa.String(255, collation="C"), nullable=False),
        sa.Column("series_code", sa.String(80), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("domain_code <> ''", name=f"ck_{prefix}_domain"),
        sa.CheckConstraint("document_reference <> ''", name=f"ck_{prefix}_document"),
        sa.CheckConstraint("formatted_number <> ''", name=f"ck_{prefix}_number"),
        sa.CheckConstraint(
            "provenance <> '' AND length(provenance) <= 4096",
            name=f"ck_{prefix}_provenance",
        ),
        sa.CheckConstraint(
            "origin IN ('allocation', 'historical', 'external')",
            name=f"ck_{prefix}_origin",
        ),
    ]


def _freeze_sql(name: str, tenant: bool) -> str:
    identity = [
        "id",
        "series_code",
        "strict_identity",
        "domain_code",
        "prefix",
        "suffix",
        "separator",
        "min_digits",
        "include_year",
        "year_digits",
        "include_month",
        "reset_policy",
        "start_value",
    ]
    if tenant:
        identity.append("tenant_id")
    enrollment_scope = (
        " OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id" if tenant else ""
    )
    new = ", ".join(f"NEW.{field}" for field in identity)
    old = ", ".join(f"OLD.{field}" for field in identity)
    return f"""
    CREATE FUNCTION mod_numbering.{name}() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.strict_identity THEN
                RAISE EXCEPTION 'strict numbering identities cannot be deleted' USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.strict_identity AND ({new}) IS DISTINCT FROM ({old}) THEN
            RAISE EXCEPTION 'strict numbering identity is immutable from creation' USING ERRCODE = 'restrict_violation';
        END IF;
        IF NOT OLD.strict_identity AND NEW.strict_identity AND (
            (NEW.id, NEW.series_code, NEW.prefix, NEW.suffix, NEW.separator, NEW.min_digits, NEW.include_year, NEW.year_digits, NEW.include_month, NEW.reset_policy, NEW.start_value)
            IS DISTINCT FROM
            (OLD.id, OLD.series_code, OLD.prefix, OLD.suffix, OLD.separator, OLD.min_digits, OLD.include_year, OLD.year_digits, OLD.include_month, OLD.reset_policy, OLD.start_value){enrollment_scope}) THEN
            RAISE EXCEPTION 'enrollment cannot change numbering configuration' USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _claim_policy_sql(name: str, domain_table: str, tenant: bool) -> str:
    tenant_predicate = "d.tenant_id = NEW.tenant_id AND " if tenant else ""
    return f"""
    CREATE FUNCTION mod_numbering.{name}() RETURNS trigger AS $$
    DECLARE partitioned boolean;
    BEGIN
        SELECT d.partitioned INTO STRICT partitioned FROM mod_numbering.{domain_table} d
            WHERE {tenant_predicate}d.domain_code = NEW.domain_code;
        IF (partitioned AND NEW.partition_key = '') OR (NOT partitioned AND NEW.partition_key <> '') THEN
            RAISE EXCEPTION 'claim partition does not match immutable domain policy' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """  # noqa: S608


def _series_extension(table: str, tenant: bool) -> None:
    op.add_column(
        table,
        sa.Column(
            "strict_identity", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        table, sa.Column("domain_code", sa.String(80), nullable=True), schema=_SCHEMA
    )
    op.create_check_constraint(
        f"ck_{table}_strict_domain",
        table,
        "strict_identity = (domain_code IS NOT NULL)",
        schema=_SCHEMA,
    )
    keys = (
        ["tenant_id", "series_code", "domain_code"]
        if tenant
        else ["series_code", "domain_code"]
    )
    domain_keys = ["tenant_id", "domain_code"] if tenant else ["domain_code"]
    domain_table = "number_domains" if tenant else "platform_number_domains"
    op.create_unique_constraint(
        f"uq_{table}_domain_identity", table, keys, schema=_SCHEMA
    )
    op.create_foreign_key(
        f"fk_{table}_domain",
        table,
        domain_table,
        domain_keys,
        domain_keys,
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
    )
    function = (
        "freeze_strict_tenant_series" if tenant else "freeze_strict_platform_series"
    )
    op.execute(_freeze_sql(function, tenant))
    op.execute(
        f"CREATE TRIGGER {table}_strict_freeze BEFORE UPDATE OR DELETE ON mod_numbering.{table} FOR EACH ROW EXECUTE FUNCTION mod_numbering.{function}();"
    )


def _protect(table: str, *, tenant: bool) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_numbering.{table} FOR EACH ROW EXECUTE FUNCTION mod_numbering.refuse_mutation();"
    )
    if tenant:
        op.execute(f"ALTER TABLE mod_numbering.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_numbering.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_numbering.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT ON mod_numbering.{table} TO app_user, platform_api;"
        )
    else:
        op.execute(
            f"GRANT SELECT, INSERT ON mod_numbering.{table} TO platform_api, app_admin;"
        )
        op.execute(f"REVOKE ALL ON mod_numbering.{table} FROM app_user;")
        # Tables are new, so no inherited per-column grant can survive creation.


def _upgrade_tenant_plane() -> None:
    op.create_table(
        "number_domains",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_domain_columns(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_number_domains_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "domain_code", name="uq_number_domains_code"),
        sa.CheckConstraint("domain_code <> ''", name="ck_number_domains_code"),
        schema=_SCHEMA,
    )
    _series_extension("number_series", True)
    op.create_table(
        "number_claims",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_claim_columns(),
        *_checks("number_claims"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_number_claims_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "domain_code"],
            [
                "mod_numbering.number_domains.tenant_id",
                "mod_numbering.number_domains.domain_code",
            ],
            name="fk_number_claims_domain",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_code", "domain_code"],
            [
                "mod_numbering.number_series.tenant_id",
                "mod_numbering.number_series.series_code",
                "mod_numbering.number_series.domain_code",
            ],
            name="fk_number_claims_series",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "domain_code",
            "partition_key",
            "formatted_number",
            name="uq_number_claims_rendered",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "domain_code",
            "partition_key",
            "document_reference",
            name="uq_number_claims_document",
        ),
        schema=_SCHEMA,
    )
    _protect("number_domains", tenant=True)
    _protect("number_claims", tenant=True)
    op.execute(_claim_policy_sql("check_tenant_claim_policy", "number_domains", True))
    op.execute(
        "CREATE TRIGGER number_claims_policy BEFORE INSERT ON mod_numbering.number_claims FOR EACH ROW EXECUTE FUNCTION mod_numbering.check_tenant_claim_policy();"
    )


def _upgrade_platform_plane() -> None:
    op.create_table(
        "platform_number_domains",
        *_domain_columns(),
        sa.UniqueConstraint("domain_code", name="uq_platform_number_domains_code"),
        sa.CheckConstraint("domain_code <> ''", name="ck_platform_number_domains_code"),
        schema=_SCHEMA,
    )
    _series_extension("platform_number_series", False)
    op.create_table(
        "platform_number_claims",
        *_claim_columns(),
        *_checks("platform_number_claims"),
        sa.ForeignKeyConstraint(
            ["domain_code"],
            ["mod_numbering.platform_number_domains.domain_code"],
            name="fk_platform_number_claims_domain",
        ),
        sa.ForeignKeyConstraint(
            ["series_code", "domain_code"],
            [
                "mod_numbering.platform_number_series.series_code",
                "mod_numbering.platform_number_series.domain_code",
            ],
            name="fk_platform_number_claims_series",
        ),
        sa.UniqueConstraint(
            "domain_code",
            "partition_key",
            "formatted_number",
            name="uq_platform_number_claims_rendered",
        ),
        sa.UniqueConstraint(
            "domain_code",
            "partition_key",
            "document_reference",
            name="uq_platform_number_claims_document",
        ),
        schema=_SCHEMA,
    )
    _protect("platform_number_domains", tenant=False)
    _protect("platform_number_claims", tenant=False)
    op.execute(
        _claim_policy_sql(
            "check_platform_claim_policy", "platform_number_domains", False
        )
    )
    op.execute(
        "CREATE TRIGGER platform_number_claims_policy BEFORE INSERT ON mod_numbering.platform_number_claims FOR EACH ROW EXECUTE FUNCTION mod_numbering.check_platform_claim_policy();"
    )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)
        _upgrade_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)
        _upgrade_platform_plane()


def downgrade() -> None:
    raise RuntimeError(
        "Numbering claims and frozen identities cannot be discarded by downgrade."
    )
