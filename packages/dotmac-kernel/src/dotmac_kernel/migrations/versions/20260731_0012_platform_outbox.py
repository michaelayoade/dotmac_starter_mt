"""Platform outbox table + relay security (WS3 platform relay).

The platform peer of the tenant outbox (0008) + its relay (0011), on a SEPARATE
table with a SEPARATE dispatcher role — the two are never combined:

- ``platform_outbox_events`` — PLATFORM catalog: **no ``tenant_id``, no tenant FK,
  no RLS**; GRANTed to ``platform_api``/``app_admin``, REVOKEd from ``app_user``
  (0009 pattern). Carries the relay lease columns from the start.
- role ``platform_outbox_dispatcher`` — LOGIN, **NOSUPERUSER, NOBYPASSRLS**, and
  **no table privilege of any kind** (distinct from both ``platform_api`` and the
  tenant ``outbox_dispatcher``).
- two hardened, schema-qualified ``SECURITY DEFINER`` functions owned by
  ``app_admin`` — ``claim_platform_outbox_batch`` (atomic FOR UPDATE SKIP LOCKED
  claim, incl. stale-lease reclaim) and ``settle_platform_outbox_event`` (records a
  terminal/retry outcome, only for a row the caller holds a live lease on).
- ``platform_outbox_dispatcher`` gets ``EXECUTE`` on exactly those two functions
  (revoked from PUBLIC) and ``USAGE`` on the schema — nothing else.

So the platform dispatcher can only claim/settle; a direct SELECT/UPDATE on any
table is ``permission denied``. The retry/backoff/dead-letter POLICY is computed by
the Python caller (``messaging.platform_relay``), reusing the tenant relay engine.

Revision ID: 0012_platform_outbox
Revises: 0011_outbox_relay_leasing
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_platform_outbox"
down_revision = "0011_outbox_relay_leasing"
branch_labels = None
depends_on = None

_TABLE = "platform_outbox_events"
_CLAIM_SIG = "public.claim_platform_outbox_batch(text, integer, integer)"
_SETTLE_SIG = (
    "public.settle_platform_outbox_event(uuid, text, text, timestamptz, integer, text)"
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("correlation_id", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("leased_by", sa.String(length=200), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_platform_outbox_events_status_available_at",
        _TABLE,
        ["status", "available_at"],
    )
    op.create_index(
        "ix_platform_outbox_events_status_leased_at",
        _TABLE,
        ["status", "leased_at"],
    )

    # Platform catalog grants — no RLS (there is no tenant to scope by).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_admin;")
    op.execute(f"REVOKE ALL ON {_TABLE} FROM app_user;")

    # Dedicated dispatcher role — least privilege, distinct from platform_api AND
    # the tenant outbox_dispatcher. Idempotent (safe if it already exists).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'platform_outbox_dispatcher'
            ) THEN
                CREATE ROLE platform_outbox_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    # Atomic claim (incl. stale-lease reclaim). SECURITY DEFINER + empty search_path;
    # runs as the owner (app_admin). No RLS here, but SECURITY DEFINER keeps the
    # dispatcher EXECUTE-only with no direct table privilege.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.claim_platform_outbox_batch(
            p_worker text, p_batch integer, p_stale_seconds integer)
        RETURNS SETOF public.platform_outbox_events
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = ''
        AS $fn$
            UPDATE public.platform_outbox_events
               SET status = 'claimed', leased_by = p_worker, leased_at = now()
             WHERE id IN (
               SELECT id FROM public.platform_outbox_events
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
        CREATE OR REPLACE FUNCTION public.settle_platform_outbox_event(
            p_id uuid, p_worker text, p_status text, p_available_at timestamptz,
            p_attempts integer, p_last_error text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = ''
        AS $fn$
        DECLARE n integer;
        BEGIN
            UPDATE public.platform_outbox_events
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

    # SECURITY DEFINER functions run as their owner (app_admin), so app_admin needs
    # privilege on the table (it owns the schema in prod; not in a postgres-owned
    # test cluster — grant explicitly; app_admin is the BYPASSRLS migrator).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_admin;")
    op.execute(f"ALTER FUNCTION {_CLAIM_SIG} OWNER TO app_admin;")
    op.execute(f"ALTER FUNCTION {_SETTLE_SIG} OWNER TO app_admin;")
    op.execute(f"REVOKE ALL ON FUNCTION {_CLAIM_SIG} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON FUNCTION {_SETTLE_SIG} FROM PUBLIC;")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_CLAIM_SIG} TO platform_outbox_dispatcher;")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_SETTLE_SIG} TO platform_outbox_dispatcher;"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO platform_outbox_dispatcher;")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_CLAIM_SIG};")
    op.execute(f"DROP FUNCTION IF EXISTS {_SETTLE_SIG};")
    op.execute("REVOKE USAGE ON SCHEMA public FROM platform_outbox_dispatcher;")
    op.execute("DROP ROLE IF EXISTS platform_outbox_dispatcher;")
    op.drop_index("ix_platform_outbox_events_status_leased_at", table_name=_TABLE)
    op.drop_index("ix_platform_outbox_events_status_available_at", table_name=_TABLE)
    op.drop_table(_TABLE)
