"""Govern the DRAFT -> VOID transition of never-posted journals.

Revision ID: ac_0003_draft_void
Revises: ac_0002_posted_content
Create Date: 2026-11-14

Replaces only the ``protect_posted_journal`` function body; triggers, tables,
permissions and policies are unchanged. A journal may become VOID only from
DRAFT, never with posting evidence, and only once an immutable ``period_events``
row of kind JOURNAL_VOID (reason, requester, distinct approver, approval
reference) names it. Every non-DRAFT journal, VOID included, stays frozen by
the existing posted-content guards.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ac_0003_draft_void"
down_revision = "ac_0002_posted_content"
branch_labels = None
depends_on = None

_VOID_GUARD = """
        CREATE OR REPLACE FUNCTION mod_accounting.protect_posted_journal()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE journal_line_count bigint;
        DECLARE ledger_line_count bigint;
        DECLARE ledger_debit numeric;
        DECLARE ledger_credit numeric;
        DECLARE ledger_original_debit numeric;
        DECLARE ledger_original_credit numeric;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a journal must be assembled as draft';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' AND OLD.status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journals cannot be deleted';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status='POSTED' THEN
            SELECT count(*) INTO journal_line_count
            FROM mod_accounting.journal_lines line
            WHERE line.tenant_id=NEW.tenant_id AND line.journal_id=NEW.id;
            SELECT count(*), COALESCE(sum(debit),0), COALESCE(sum(credit),0),
                   COALESCE(sum(original_debit),0), COALESCE(sum(original_credit),0)
            INTO ledger_line_count, ledger_debit, ledger_credit,
                 ledger_original_debit, ledger_original_credit
            FROM mod_accounting.posted_ledger_lines ledger
            WHERE ledger.tenant_id=NEW.tenant_id AND ledger.journal_id=NEW.id;
            IF journal_line_count<2 OR ledger_line_count<>journal_line_count
               OR ledger_debit<=0 OR ledger_debit<>ledger_credit
               OR ledger_debit<>NEW.total_debit_functional
               OR ledger_credit<>NEW.total_credit_functional
               OR ledger_original_debit<=0
               OR ledger_original_debit<>ledger_original_credit
               OR ledger_original_debit<>NEW.total_debit
               OR ledger_original_credit<>NEW.total_credit
               OR NEW.approval_reference IS NULL OR NEW.posted_by IS NULL OR NEW.posted_at IS NULL THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal requires complete balanced ledger evidence';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status='VOID' THEN
            -- Only never-posted drafts, and only with an appended void event
            -- naming this journal, its reason and two distinct actors.
            IF OLD.posted_at IS NOT NULL OR OLD.posted_by IS NOT NULL
               OR OLD.approval_reference IS NOT NULL OR OLD.reversal_journal_id IS NOT NULL
               OR EXISTS (
                 SELECT 1 FROM mod_accounting.posted_ledger_lines ledger
                 WHERE ledger.tenant_id=OLD.tenant_id AND ledger.journal_id=OLD.id
               )
               OR NOT EXISTS (
                 SELECT 1 FROM mod_accounting.period_events event
                 WHERE event.tenant_id=OLD.tenant_id
                   AND event.period_id=OLD.fiscal_period_id
                   AND event.event_kind='JOURNAL_VOID'
                   AND event.from_status='DRAFT' AND event.to_status='VOID'
                   AND event.evidence->>'journal_id'=OLD.id::text
                   AND length(btrim(event.evidence->>'reason'))>0
                   AND length(btrim(event.actor_ref))>0
                   AND length(btrim(event.approval_reference))>0
                   AND length(btrim(event.evidence->>'approved_by'))>0
                   AND lower(btrim(event.actor_ref))<>lower(btrim(event.evidence->>'approved_by'))
               )
               OR (to_jsonb(NEW)-ARRAY['status','updated_at']) <> (to_jsonb(OLD)-ARRAY['status','updated_at']) THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='void requires a never-posted draft and a recorded void event';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status NOT IN ('DRAFT','VOID') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid draft journal transition';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status<>'DRAFT' THEN
            IF OLD.status='POSTED' AND NEW.status='REVERSED'
               AND OLD.reversal_journal_id IS NULL AND NEW.reversal_journal_id IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM mod_accounting.journal_entries reversal
                 WHERE reversal.tenant_id=OLD.tenant_id
                   AND reversal.id=NEW.reversal_journal_id
                   AND reversal.reverses_journal_id=OLD.id
                   AND reversal.status='POSTED'
               )
               AND (to_jsonb(NEW)-ARRAY['status','reversal_journal_id','updated_at']) = (to_jsonb(OLD)-ARRAY['status','reversal_journal_id','updated_at']) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal content is immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
"""

_PREDECESSOR_GUARD = """
        CREATE OR REPLACE FUNCTION mod_accounting.protect_posted_journal()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE journal_line_count bigint;
        DECLARE ledger_line_count bigint;
        DECLARE ledger_debit numeric;
        DECLARE ledger_credit numeric;
        DECLARE ledger_original_debit numeric;
        DECLARE ledger_original_credit numeric;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a journal must be assembled as draft';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' AND OLD.status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journals cannot be deleted';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status='POSTED' THEN
            SELECT count(*) INTO journal_line_count
            FROM mod_accounting.journal_lines line
            WHERE line.tenant_id=NEW.tenant_id AND line.journal_id=NEW.id;
            SELECT count(*), COALESCE(sum(debit),0), COALESCE(sum(credit),0),
                   COALESCE(sum(original_debit),0), COALESCE(sum(original_credit),0)
            INTO ledger_line_count, ledger_debit, ledger_credit,
                 ledger_original_debit, ledger_original_credit
            FROM mod_accounting.posted_ledger_lines ledger
            WHERE ledger.tenant_id=NEW.tenant_id AND ledger.journal_id=NEW.id;
            IF journal_line_count<2 OR ledger_line_count<>journal_line_count
               OR ledger_debit<=0 OR ledger_debit<>ledger_credit
               OR ledger_debit<>NEW.total_debit_functional
               OR ledger_credit<>NEW.total_credit_functional
               OR ledger_original_debit<=0
               OR ledger_original_debit<>ledger_original_credit
               OR ledger_original_debit<>NEW.total_debit
               OR ledger_original_credit<>NEW.total_credit
               OR NEW.approval_reference IS NULL OR NEW.posted_by IS NULL OR NEW.posted_at IS NULL THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal requires complete balanced ledger evidence';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status NOT IN ('DRAFT','VOID') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid draft journal transition';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status<>'DRAFT' THEN
            IF OLD.status='POSTED' AND NEW.status='REVERSED'
               AND OLD.reversal_journal_id IS NULL AND NEW.reversal_journal_id IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM mod_accounting.journal_entries reversal
                 WHERE reversal.tenant_id=OLD.tenant_id
                   AND reversal.id=NEW.reversal_journal_id
                   AND reversal.reverses_journal_id=OLD.id
                   AND reversal.status='POSTED'
               )
               AND (to_jsonb(NEW)-ARRAY['status','reversal_journal_id','updated_at']) = (to_jsonb(OLD)-ARRAY['status','reversal_journal_id','updated_at']) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal content is immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
"""


def _lock() -> str:
    bind = op.get_bind()
    previous = str(
        bind.scalar(sa.text("SELECT pg_catalog.current_setting('lock_timeout')"))
    )
    bind.execute(sa.text("SELECT pg_catalog.set_config('lock_timeout', '5s', true)"))
    op.execute(
        "LOCK TABLE mod_accounting.journal_entries " "IN SHARE ROW EXCLUSIVE MODE"
    )
    return previous


def _restore_timeout(previous: str) -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_catalog.set_config('lock_timeout', :previous, true)"),
        {"previous": previous},
    )


def upgrade() -> None:
    previous = _lock()
    op.execute(_VOID_GUARD)
    _restore_timeout(previous)


def downgrade() -> None:
    previous = _lock()
    # Same policy as payables pa_0003/pa_0004: once guarded rows exist the
    # downgrade would silently drop the void authority they rely on.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mod_accounting.journal_entries "
            "WHERE status='VOID') "
            "OR EXISTS (SELECT 1 FROM mod_accounting.period_events "
            "WHERE event_kind='JOURNAL_VOID')"
        )
    ):
        raise RuntimeError(
            "cannot downgrade ac_0003: voided journals or void events exist"
        )
    op.execute(_PREDECESSOR_GUARD)
    _restore_timeout(previous)
