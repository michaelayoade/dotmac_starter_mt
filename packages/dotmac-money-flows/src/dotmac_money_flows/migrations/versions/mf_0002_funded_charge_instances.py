"""Create the tenant FundedCharge (F2) instance table.

Revision ID: mf_0002_funded_charge_instances
Revises: mf_0001_settle_instances

Instance state only: no amount, currency or balance column and no foreign key
to another owner. Rows are never deleted (the runtime role holds no DELETE).
There is no deadline, timer or cancellation: ``deadline_at`` and
``resolution_ref`` exist only because every instance table carries the common
columns, and CHECK constraints pin both to NULL. A trigger holds the transition
graph (insert only as pending, discharged or attention_required at version 0;
pending to discharged, attention_required or withdrawn; attention_required to
attention_required, pending, discharged or withdrawn; discharged and withdrawn
terminal; version advances by exactly one) and refuses identity-column changes
and any change to a recorded obligation, document or credit-draw reference.
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "mf_0002_funded_charge_instances"
down_revision = "mf_0001_settle_instances"
branch_labels = None

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_mflows"
_TABLE = "funded_charge_instances"
_STATES = ("pending", "discharged", "attention_required", "withdrawn")


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)

    states = ", ".join(f"'{state}'" for state in _STATES)
    op.create_table(
        "funded_charge_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
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
        sa.Column("charge_ref", sa.String(700), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("service_ref", sa.String(255), nullable=True),
        sa.Column("obligation_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("credit_draw_id", sa.Uuid(), nullable=True),
        sa.Column("funding_settlement_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_funded_charge_instances_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_funded_charge_instances_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "charge_ref", name="uq_funded_charge_instances_charge"
        ),
        sa.CheckConstraint(
            f"state IN ({states})", name="ck_funded_charge_instances_state"
        ),
        sa.CheckConstraint(
            "(reason_code IS NOT NULL) = (state IN ('attention_required', 'withdrawn'))",
            name="ck_funded_charge_instances_reason_iff_attention_or_withdrawn",
        ),
        sa.CheckConstraint(
            "deadline_at IS NULL", name="ck_funded_charge_instances_no_deadline"
        ),
        sa.CheckConstraint(
            "resolution_ref IS NULL", name="ck_funded_charge_instances_no_resolution"
        ),
        sa.CheckConstraint(
            "(state = 'discharged' AND obligation_id IS NOT NULL "
            "AND document_id IS NOT NULL AND credit_draw_id IS NOT NULL) "
            "OR (state <> 'discharged' AND obligation_id IS NULL "
            "AND document_id IS NULL AND credit_draw_id IS NULL)",
            name="ck_funded_charge_instances_discharge_refs",
        ),
        sa.CheckConstraint(
            "octet_length(charge_ref) <= 1024",
            name="ck_funded_charge_instances_charge_ref_bytes",
        ),
        sa.CheckConstraint("version >= 0", name="ck_funded_charge_instances_version"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_funded_charge_instances_account_order",
        _TABLE,
        ["tenant_id", "billing_account_id", "state", "created_at", "id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_funded_charge_instances_service",
        _TABLE,
        ["tenant_id", "service_ref"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_funded_charge_instances_state_order",
        _TABLE,
        ["tenant_id", "state", "created_at", "id"],
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
        CREATE FUNCTION {_SCHEMA}.guard_funded_charge_instance()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.state NOT IN ('pending', 'discharged', 'attention_required') THEN
                    RAISE EXCEPTION
                        'funded charge instance may only be inserted as pending, discharged or attention_required'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                IF NEW.version <> 0 THEN
                    RAISE EXCEPTION 'funded charge instance must be inserted at version 0'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.charge_ref IS DISTINCT FROM OLD.charge_ref
               OR NEW.billing_account_id IS DISTINCT FROM OLD.billing_account_id
               OR NEW.service_ref IS DISTINCT FROM OLD.service_ref
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'funded charge instance identity is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- Defensive: discharge refs exist only on terminal rows (the CHECK
            -- ties them to state = 'discharged'), so this is unreachable today
            -- and keeps the refs fixed if the terminal rule is ever relaxed.
            IF (OLD.obligation_id IS NOT NULL
                    AND NEW.obligation_id IS DISTINCT FROM OLD.obligation_id)
               OR (OLD.document_id IS NOT NULL
                    AND NEW.document_id IS DISTINCT FROM OLD.document_id)
               OR (OLD.credit_draw_id IS NOT NULL
                    AND NEW.credit_draw_id IS DISTINCT FROM OLD.credit_draw_id) THEN
                RAISE EXCEPTION
                    'funded charge instance discharge references are never changed once set'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.state IN ('discharged', 'withdrawn') THEN
                RAISE EXCEPTION 'funded charge instance in terminal state % is immutable',
                    OLD.state USING ERRCODE = 'restrict_violation';
            END IF;
            IF NOT (
                (OLD.state = 'pending'
                    AND NEW.state IN ('discharged', 'attention_required', 'withdrawn'))
                OR (OLD.state = 'attention_required'
                    AND NEW.state IN ('attention_required', 'pending', 'discharged', 'withdrawn'))
            ) THEN
                RAISE EXCEPTION 'funded charge instance transition % to % is not allowed',
                    OLD.state, NEW.state USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'funded charge instance version must advance by exactly one'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            NEW.updated_at := pg_catalog.now();
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"CREATE TRIGGER funded_charge_instances_guard "
        f"BEFORE INSERT OR UPDATE ON {_SCHEMA}.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.guard_funded_charge_instance()"
    )


def downgrade() -> None:
    # Instance rows are the only evidence a money flow ran; never drop them.
    op.execute(
        f"SET LOCAL row_security = off; LOCK TABLE {_SCHEMA}.{_TABLE} IN ACCESS EXCLUSIVE MODE;"
    )
    evidence = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM mod_mflows.funded_charge_instances LIMIT 1"))
        .first()
    )
    if evidence is not None:
        raise RuntimeError("cannot downgrade money flows while instance rows exist")
    op.execute(f"DROP TRIGGER funded_charge_instances_guard ON {_SCHEMA}.{_TABLE};")
    op.execute(f"DROP FUNCTION {_SCHEMA}.guard_funded_charge_instance();")
    op.drop_table(_TABLE, schema=_SCHEMA)
