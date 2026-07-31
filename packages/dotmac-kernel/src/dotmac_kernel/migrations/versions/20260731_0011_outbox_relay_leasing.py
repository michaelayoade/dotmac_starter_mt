"""Outbox relay leasing schema + security (WS3 slice 2, PR 1).

Adds the relay's lease columns to ``outbox_events`` and the privilege boundary the
relay's cross-tenant drain requires (design brief
``docs/superpowers/reviews/2026-07-31-ws3-slice2-outbox-relay-design.md``):

- lease columns ``leased_by`` / ``leased_at`` + a stale-lease reclaim index;
- role ``outbox_dispatcher`` — LOGIN, **NOSUPERUSER, NOBYPASSRLS**, and (crucially)
  **no table privilege of any kind**;
- two hardened, schema-qualified ``SECURITY DEFINER`` functions owned by
  ``app_admin`` — ``claim_outbox_batch`` (atomic FOR UPDATE SKIP LOCKED claim,
  incl. stale-lease reclaim) and ``settle_outbox_event`` (records a terminal/retry
  outcome, only for a row the caller holds a live lease on);
- ``outbox_dispatcher`` gets ``EXECUTE`` on exactly those two functions (revoked
  from PUBLIC) and ``USAGE`` on the schema — nothing else.

So the dispatcher can only claim/settle; a direct SELECT/UPDATE on any table is
``permission denied``. The retry/backoff/dead-letter POLICY (what status/attempts
to settle with) is computed by the Python caller (PR 2), keeping these functions
mechanical.

Revision ID: 0011_outbox_relay_leasing
Revises: 0010_tenant_entitlements
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_outbox_relay_leasing"
down_revision = "0010_tenant_entitlements"
branch_labels = None
depends_on = None

_CLAIM_SIG = "public.claim_outbox_batch(text, integer, integer)"
_SETTLE_SIG = "public.settle_outbox_event(uuid, text, text, timestamptz, integer, text)"


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("leased_by", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_events_status_leased_at",
        "outbox_events",
        ["status", "leased_at"],
    )

    # Dedicated dispatcher role — least privilege, no BYPASSRLS. Idempotent so the
    # migration is safe whether or not the role already exists cluster-wide.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'outbox_dispatcher') THEN
                CREATE ROLE outbox_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    # Atomic claim (incl. stale-lease reclaim). SECURITY DEFINER + empty search_path;
    # runs as the owner (app_admin, BYPASSRLS) so it sees all tenants' rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.claim_outbox_batch(
            p_worker text, p_batch integer, p_stale_seconds integer)
        RETURNS SETOF public.outbox_events
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = ''
        AS $fn$
            UPDATE public.outbox_events
               SET status = 'claimed', leased_by = p_worker, leased_at = now()
             WHERE id IN (
               SELECT id FROM public.outbox_events
                WHERE (status = 'pending' AND available_at <= now())
                   OR (status = 'claimed'
                       AND leased_at < now() - make_interval(secs => p_stale_seconds))
                ORDER BY available_at
                FOR UPDATE SKIP LOCKED
                LIMIT p_batch
             )
             RETURNING *;
        $fn$;
        """
    )

    # Settle exactly one row the caller holds a live lease on. Mechanical: the
    # caller decides the outcome (status/available_at/attempts/last_error).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.settle_outbox_event(
            p_id uuid, p_worker text, p_status text, p_available_at timestamptz,
            p_attempts integer, p_last_error text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = ''
        AS $fn$
        DECLARE n integer;
        BEGIN
            UPDATE public.outbox_events
               SET status = p_status,
                   attempts = p_attempts,
                   last_error = p_last_error,
                   available_at = COALESCE(p_available_at, available_at),
                   sent_at = CASE WHEN p_status = 'sent' THEN now() ELSE sent_at END,
                   leased_by = CASE WHEN p_status IN ('sent', 'dead')
                                    THEN NULL ELSE leased_by END,
                   leased_at = CASE WHEN p_status IN ('sent', 'dead')
                                    THEN NULL ELSE leased_at END
             WHERE id = p_id AND leased_by = p_worker AND status = 'claimed';
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n = 1;
        END
        $fn$;
        """
    )

    # The SECURITY DEFINER functions run as their owner (app_admin), so app_admin
    # needs privilege on the table. It owns the schema in production; in a
    # postgres-owned test cluster it does not, so grant it explicitly (app_admin is
    # the BYPASSRLS migrator — this widens nothing for online roles).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON outbox_events TO app_admin;"
    )

    # Own the functions as app_admin (the migration role differs across
    # environments), then hand the dispatcher EXECUTE-ONLY — nothing on any table.
    op.execute(f"ALTER FUNCTION {_CLAIM_SIG} OWNER TO app_admin;")
    op.execute(f"ALTER FUNCTION {_SETTLE_SIG} OWNER TO app_admin;")
    op.execute(f"REVOKE ALL ON FUNCTION {_CLAIM_SIG} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON FUNCTION {_SETTLE_SIG} FROM PUBLIC;")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_CLAIM_SIG} TO outbox_dispatcher;")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SETTLE_SIG} TO outbox_dispatcher;")
    op.execute("GRANT USAGE ON SCHEMA public TO outbox_dispatcher;")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_CLAIM_SIG};")
    op.execute(f"DROP FUNCTION IF EXISTS {_SETTLE_SIG};")
    op.execute("REVOKE USAGE ON SCHEMA public FROM outbox_dispatcher;")
    op.execute("DROP ROLE IF EXISTS outbox_dispatcher;")
    op.drop_index("ix_outbox_events_status_leased_at", table_name="outbox_events")
    op.drop_column("outbox_events", "leased_at")
    op.drop_column("outbox_events", "leased_by")
