"""Govern account maintenance: recode, flag correction, retire, activate.

Revision ID: ac_0004_account_maintenance
Revises: ac_0003_draft_void
Create Date: 2026-11-18

Adds the append-only ``account_changes`` history and a guard on ``accounts``:
the account ``id`` and ``tenant_id`` never change, and ``code``, ``kind``,
``posting_allowed`` and ``is_active`` change only together with an
``account_changes`` row written in the same transaction that records exactly
that before/after. Posted ledger lines are untouched: they keep the
``account_code`` they were posted under as history.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ac_0004_account_maintenance"
down_revision = "ac_0003_draft_void"
branch_labels = None
depends_on = None

_SCHEMA = "mod_accounting"

_GUARD = """
        CREATE FUNCTION mod_accounting.protect_account_maintenance()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id<>OLD.id OR NEW.tenant_id<>OLD.tenant_id THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='account identity is immutable';
          END IF;
          IF NEW.category_id<>OLD.category_id
             OR NEW.normal_balance<>OLD.normal_balance
             OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
             OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='account classification is immutable';
          END IF;
          IF NEW.code<>OLD.code OR NEW.kind<>OLD.kind
             OR NEW.posting_allowed<>OLD.posting_allowed
             OR NEW.is_active<>OLD.is_active THEN
            IF NOT EXISTS (
              SELECT 1 FROM mod_accounting.account_changes change
              WHERE change.tenant_id=OLD.tenant_id
                AND change.account_id=OLD.id
                AND change.applied_txid=pg_catalog.txid_current()
                AND change.old_code=OLD.code AND change.new_code=NEW.code
                AND change.old_kind=OLD.kind AND change.new_kind=NEW.kind
                AND change.old_posting_allowed=OLD.posting_allowed
                AND change.new_posting_allowed=NEW.posting_allowed
                AND change.old_is_active=OLD.is_active
                AND change.new_is_active=NEW.is_active
                AND length(pg_catalog.btrim(change.reason))>0
                AND length(pg_catalog.btrim(change.actor_ref))>0
            ) THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='account code and flag changes require a recorded account change';
            END IF;
          END IF;
          RETURN NEW;
        END; $$;
"""

_STAMP = """
        CREATE FUNCTION mod_accounting.stamp_account_change()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          NEW.applied_txid := pg_catalog.txid_current();
          RETURN NEW;
        END; $$;
"""


def upgrade() -> None:
    op.create_table(
        "account_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("change_kind", sa.String(24), nullable=False),
        sa.Column("old_code", sa.String(40), nullable=False),
        sa.Column("new_code", sa.String(40), nullable=False),
        sa.Column("old_kind", sa.String(24), nullable=False),
        sa.Column("new_kind", sa.String(24), nullable=False),
        sa.Column("old_posting_allowed", sa.Boolean(), nullable=False),
        sa.Column("new_posting_allowed", sa.Boolean(), nullable=False),
        sa.Column("old_is_active", sa.Boolean(), nullable=False),
        sa.Column("new_is_active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("applied_txid", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_account_changes_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_account_changes_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["mod_accounting.accounts.tenant_id", "mod_accounting.accounts.id"],
            ondelete="RESTRICT",
            name="fk_account_changes_tenant_account",
        ),
        sa.CheckConstraint(
            "change_kind IN ('RECODE','FLAGS','RETIRE','ACTIVATE')",
            name="ck_account_changes_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_account_changes_tenant_account",
        "account_changes",
        ["tenant_id", "account_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute(_STAMP)
    op.execute(_GUARD)
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.stamp_account_change() FROM PUBLIC;"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_account_maintenance() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER account_changes_stamp BEFORE INSERT ON mod_accounting.account_changes FOR EACH ROW EXECUTE FUNCTION mod_accounting.stamp_account_change();"
    )
    op.execute(
        "CREATE TRIGGER account_changes_immutable BEFORE UPDATE OR DELETE ON mod_accounting.account_changes FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_immutable_accounting_evidence();"
    )
    op.execute(
        "CREATE TRIGGER accounts_maintenance_guard BEFORE UPDATE ON mod_accounting.accounts FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_account_maintenance();"
    )
    op.execute("ALTER TABLE mod_accounting.account_changes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.account_changes FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY account_changes_tenant_isolation ON mod_accounting.account_changes USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_accounting.account_changes TO app_user;")


def downgrade() -> None:
    bind = op.get_bind()
    previous = str(
        bind.scalar(sa.text("SELECT pg_catalog.current_setting('lock_timeout')"))
    )
    bind.execute(sa.text("SELECT pg_catalog.set_config('lock_timeout', '5s', true)"))
    op.execute("LOCK TABLE mod_accounting.accounts IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE mod_accounting.account_changes IN ACCESS EXCLUSIVE MODE")
    # The history is the only record of why a code or flag differs from what it
    # was posted under; dropping it would erase that evidence.
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM mod_accounting.account_changes)")
    ):
        raise RuntimeError("cannot downgrade ac_0004: account change history exists")
    op.execute(
        "DROP TRIGGER IF EXISTS accounts_maintenance_guard ON mod_accounting.accounts;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS account_changes_immutable ON mod_accounting.account_changes;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS account_changes_stamp ON mod_accounting.account_changes;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_accounting.protect_account_maintenance();")
    op.execute("DROP FUNCTION IF EXISTS mod_accounting.stamp_account_change();")
    op.drop_table("account_changes", schema=_SCHEMA)
    bind.execute(
        sa.text("SELECT pg_catalog.set_config('lock_timeout', :previous, true)"),
        {"previous": previous},
    )
