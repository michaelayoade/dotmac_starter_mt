"""Create the tenant Settle instance table.

Revision ID: mf_0001_settle_instances
Revises: (lineage root)

Instance state only: no amount, currency or balance column and no foreign key
to another owner. Rows are never deleted (the runtime role holds no DELETE) and
a trigger holds the transition graph (insert only as settled, refused or
unapplied at version 0; refused to refused, settled or unapplied; unapplied to
resolved; settled and resolved terminal; version advances by exactly one) and
refuses identity-column changes and any change to an accepted settlement_id.
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "mf_0001_settle_instances"
down_revision = None
branch_labels = ("money_flows",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_mflows"
_TABLE = "settle_instances"
_STATES = ("settled", "refused", "unapplied", "resolved")


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA};")
    op.execute(f"REVOKE ALL ON SCHEMA {_SCHEMA} FROM PUBLIC;")
    op.execute(f"GRANT USAGE ON SCHEMA {_SCHEMA} TO app_user, app_admin;")

    states = ", ".join(f"'{state}'" for state in _STATES)
    op.create_table(
        "settle_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("definition_version", sa.String(80), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor_ref", sa.String(160), nullable=True),
        sa.Column("resolution_ref", sa.String(160), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=True),
        sa.Column("settlement_id", sa.Uuid(), nullable=True),
        sa.Column("withholding_discharge_ref", sa.String(160), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_settle_instances_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_settle_instances_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "confirmation_id", name="uq_settle_instances_confirmation"
        ),
        sa.CheckConstraint(f"state IN ({states})", name="ck_settle_instances_state"),
        sa.CheckConstraint(
            "(reason_code IS NOT NULL) = (state IN ('refused', 'unapplied', 'resolved'))",
            name="ck_settle_instances_reason_unless_settled",
        ),
        sa.CheckConstraint(
            "(state = 'refused' AND settlement_id IS NULL) OR (state IN ('settled', 'unapplied') AND settlement_id IS NOT NULL) OR state = 'resolved'",
            name="ck_settle_instances_settlement_kept",
        ),
        sa.CheckConstraint(
            "(resolution_ref IS NOT NULL) = (state = 'resolved')",
            name="ck_settle_instances_resolution_iff_resolved",
        ),
        sa.CheckConstraint("version >= 0", name="ck_settle_instances_version"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_settle_instances_tenant_state",
        _TABLE,
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_SCHEMA}.{_TABLE} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    # Rows are never deleted: the runtime role gets no DELETE.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_SCHEMA}.{_TABLE} TO app_user;")

    op.execute(
        f"""
        CREATE FUNCTION {_SCHEMA}.guard_settle_instance()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state NOT IN ('settled', 'refused', 'unapplied') THEN
                    RAISE EXCEPTION
                        'settle instance may only be inserted as settled, refused or unapplied'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                IF NEW.version <> 0 THEN
                    RAISE EXCEPTION 'settle instance must be inserted at version 0'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.confirmation_id IS DISTINCT FROM OLD.confirmation_id
               OR NEW.intent_id IS DISTINCT FROM OLD.intent_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'settle instance identity is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.state IN ('settled', 'resolved') THEN
                RAISE EXCEPTION 'settle instance in terminal state % is immutable',
                    OLD.state USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.settlement_id IS NOT NULL
               AND NEW.settlement_id IS DISTINCT FROM OLD.settlement_id THEN
                RAISE EXCEPTION 'settle instance settlement_id is never changed once set'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NOT (
                (OLD.state = 'refused'
                    AND NEW.state IN ('refused', 'settled', 'unapplied'))
                OR (OLD.state = 'unapplied' AND NEW.state = 'resolved')
            ) THEN
                RAISE EXCEPTION 'settle instance transition % to % is not allowed',
                    OLD.state, NEW.state USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'settle instance version must advance by exactly one'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            NEW.updated_at := pg_catalog.now();
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"CREATE TRIGGER settle_instances_guard "
        f"BEFORE INSERT OR UPDATE ON {_SCHEMA}.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.guard_settle_instance()"
    )


def downgrade() -> None:
    # Instance rows are the only evidence a money flow ran; never drop them.
    op.execute(
        f"SET LOCAL row_security = off; LOCK TABLE {_SCHEMA}.{_TABLE} IN ACCESS EXCLUSIVE MODE;"
    )
    evidence = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM mod_mflows.settle_instances LIMIT 1"))
        .first()
    )
    if evidence is not None:
        raise RuntimeError("cannot downgrade money flows while instance rows exist")
    op.execute(f"DROP TRIGGER settle_instances_guard ON {_SCHEMA}.{_TABLE};")
    op.execute(f"DROP FUNCTION {_SCHEMA}.guard_settle_instance();")
    op.drop_table(_TABLE, schema=_SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA};")
