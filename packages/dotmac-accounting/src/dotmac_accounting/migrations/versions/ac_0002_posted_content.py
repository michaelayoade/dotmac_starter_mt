"""Serialize journal source writes with posting and freeze both ancestries.

Revision ID: ac_0002_posted_content
Revises: ac_0001_accounting
Create Date: 2026-10-13

The published root remains byte-for-byte unchanged. This replaces its invoker
trigger function without replacing triggers, permissions, or tenant policies.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ac_0002_posted_content"
down_revision = "ac_0001_accounting"
branch_labels = None
depends_on = None

_GUARD = """
CREATE OR REPLACE FUNCTION mod_accounting.protect_journal_content()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  parent_tenants uuid[] := ARRAY[]::uuid[];
  parent_ids uuid[] := ARRAY[]::uuid[];
  line_tenants uuid[] := ARRAY[]::uuid[];
  line_ids uuid[] := ARRAY[]::uuid[];
  requested record;
  parent_id uuid;
  current_status text;
BEGIN
  IF TG_TABLE_NAME='journal_lines' THEN
    IF TG_OP IN ('UPDATE','DELETE') THEN
      parent_tenants := array_append(parent_tenants, OLD.tenant_id);
      parent_ids := array_append(parent_ids, OLD.journal_id);
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
      parent_tenants := array_append(parent_tenants, NEW.tenant_id);
      parent_ids := array_append(parent_ids, NEW.journal_id);
    END IF;
  ELSE
    IF TG_OP IN ('UPDATE','DELETE') THEN
      line_tenants := array_append(line_tenants, OLD.tenant_id);
      line_ids := array_append(line_ids, OLD.journal_line_id);
    END IF;
    IF TG_OP IN ('INSERT','UPDATE') THEN
      line_tenants := array_append(line_tenants, NEW.tenant_id);
      line_ids := array_append(line_ids, NEW.journal_line_id);
    END IF;
    -- Resolve ancestry FROM the locked tuple, including after a concurrent
    -- draft reparent. NO KEY UPDATE excludes reparenting but is compatible
    -- with posting's source-line FK KEY SHARE; FOR UPDATE would deadlock it.
    FOR requested IN
      SELECT DISTINCT tenant_id, id
      FROM unnest(line_tenants, line_ids) AS refs(tenant_id, id)
      ORDER BY tenant_id, id
    LOOP
      SELECT line.journal_id INTO parent_id
      FROM mod_accounting.journal_lines line
      WHERE line.tenant_id=requested.tenant_id AND line.id=requested.id
      FOR NO KEY UPDATE;
      IF NOT FOUND THEN
        -- An FK cascade deletes its parent first. Its preceding BEFORE
        -- DELETE guard already required a draft. No INSERT/UPDATE or
        -- top-level DELETE may use missing/invisible ancestry as authority.
        IF TG_OP='DELETE' AND pg_trigger_depth()>1 THEN CONTINUE; END IF;
        RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
          MESSAGE='journal content parent is missing or inaccessible';
      END IF;
      parent_tenants := array_append(parent_tenants, requested.tenant_id);
      parent_ids := array_append(parent_ids, parent_id);
    END LOOP;
  END IF;
  FOR requested IN
    SELECT DISTINCT tenant_id, id
    FROM unnest(parent_tenants, parent_ids) AS refs(tenant_id, id)
    ORDER BY tenant_id, id
  LOOP
    SELECT journal.status INTO current_status
    FROM mod_accounting.journal_entries journal
    WHERE journal.tenant_id=requested.tenant_id AND journal.id=requested.id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
      IF TG_OP='DELETE' AND pg_trigger_depth()>1 THEN CONTINUE; END IF;
      RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
        MESSAGE='journal content parent is missing or inaccessible';
    END IF;
    IF current_status<>'DRAFT' THEN
      RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
        MESSAGE='posted journal lines are immutable';
    END IF;
  END LOOP;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END; $$;
"""


def _lock(*, downgrade: bool = False) -> str:
    bind = op.get_bind()
    previous = str(
        bind.scalar(sa.text("SELECT pg_catalog.current_setting('lock_timeout')"))
    )
    bind.execute(sa.text("SELECT pg_catalog.set_config('lock_timeout', '5s', true)"))
    # Drain predecessor parent/child writers before changing their protocol.
    # These locks block DML but permit old SELECT and FK checks to finish.
    op.execute(
        "LOCK TABLE mod_accounting.journal_entries, "
        "mod_accounting.journal_lines, mod_accounting.journal_line_dimensions "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    if downgrade:
        op.execute(
            "LOCK TABLE mod_accounting.posted_ledger_lines, "
            "mod_accounting.posted_ledger_dimensions IN SHARE ROW EXCLUSIVE MODE"
        )
    return previous


def _restore_timeout(previous: str) -> None:
    # Failure rolls back SET LOCAL with transactional DDL. On success restore
    # explicitly so subsequent migration owners inherit no timeout change.
    op.get_bind().execute(
        sa.text("SELECT pg_catalog.set_config('lock_timeout', :previous, true)"),
        {"previous": previous},
    )


def upgrade() -> None:
    previous = _lock()
    op.execute(_GUARD)
    _restore_timeout(previous)


def downgrade() -> None:
    previous = _lock(downgrade=True)
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mod_accounting.journal_entries "
            "WHERE status<>'DRAFT') "
            "OR EXISTS (SELECT 1 FROM mod_accounting.posted_ledger_lines) "
            "OR EXISTS (SELECT 1 FROM mod_accounting.posted_ledger_dimensions)"
        )
    ):
        raise RuntimeError(
            "cannot downgrade ac_0002: non-draft journals or posted evidence "
            "would lose source-content protection"
        )
    op.execute(_PREDECESSOR_GUARD)
    _restore_timeout(previous)


# Exact predecessor behavior, usable only while no frozen facts exist.
_PREDECESSOR_GUARD = """
        CREATE OR REPLACE FUNCTION mod_accounting.protect_journal_content()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE target_journal uuid;
        DECLARE target_tenant uuid;
        DECLARE current_status text;
        BEGIN
          IF TG_TABLE_NAME='journal_lines' THEN
            IF TG_OP='DELETE' THEN
              target_journal=OLD.journal_id;
              target_tenant=OLD.tenant_id;
            ELSE
              target_journal=NEW.journal_id;
              target_tenant=NEW.tenant_id;
            END IF;
          ELSE
            IF TG_OP='DELETE' THEN
              SELECT line.journal_id, line.tenant_id
              INTO target_journal, target_tenant
              FROM mod_accounting.journal_lines line
              WHERE line.id=OLD.journal_line_id AND line.tenant_id=OLD.tenant_id;
            ELSE
              SELECT line.journal_id, line.tenant_id
              INTO target_journal, target_tenant
              FROM mod_accounting.journal_lines line
              WHERE line.id=NEW.journal_line_id AND line.tenant_id=NEW.tenant_id;
            END IF;
          END IF;
          SELECT status INTO current_status FROM mod_accounting.journal_entries
          WHERE id=target_journal AND tenant_id=target_tenant;
          IF current_status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal lines are immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
        """
