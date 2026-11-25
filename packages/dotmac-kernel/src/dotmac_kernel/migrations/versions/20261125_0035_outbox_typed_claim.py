"""Event-type-filtered outbox claim function (review R2 F1). NO GRANT HERE.

`claim_outbox_batch` claims every pending tenant outbox row whatever its type, so
a consumer that handles ONE event type (the durable-timers job runner) either
dead-letters foreign events or, in the other direction, has its own events
claimed and settled by an unrelated relay. This revision adds the type-scoped
peer:

    public.claim_outbox_batch_by_type(worker text, batch integer,
                                      stale_seconds integer, event_types text[])

It is the existing claim with ONE added predicate, `event_type = ANY(...)`,
applied to BOTH arms of the claim (pending-and-due AND stale-lease reclaim), so a
foreign row is never leased, even when its lease is stale. A NULL or empty array
matches nothing (fail closed); it never means "all types".

Same hardening as `claim_outbox_batch` (0011): SECURITY DEFINER, empty
`search_path`, owned by `app_admin`, EXECUTE revoked from PUBLIC. The existing
three-argument function is untouched, so `outbox_relay.v1` and its verifier are
unchanged.

This revision grants EXECUTE to NOBODY. The one new grant to `outbox_dispatcher`
is its own revision, `0036_typed_claim_grant`, so the policy change is a
separately reviewable (and separately revertible) hunk.

Revision ID: 0035_outbox_typed_claim
Revises: 0034_receipts_platform_ro
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op

revision = "0035_outbox_typed_claim"
down_revision = "0034_receipts_platform_ro"
branch_labels = None
depends_on = None

_SIG = "public.claim_outbox_batch_by_type(text, integer, integer, text[])"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.claim_outbox_batch_by_type(
            p_worker text, p_batch integer, p_stale_seconds integer,
            p_event_types text[])
        RETURNS SETOF public.outbox_events
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = ''
        AS $fn$
            UPDATE public.outbox_events
               SET status = 'claimed', leased_by = p_worker, leased_at = now()
             WHERE id IN (
               SELECT id FROM public.outbox_events
                WHERE event_type = ANY (p_event_types)
                  AND ((status = 'pending' AND available_at <= now())
                    OR (status = 'claimed'
                        AND leased_at < now() - make_interval(secs => p_stale_seconds)))
                ORDER BY available_at
                FOR UPDATE SKIP LOCKED
                LIMIT p_batch
             )
             RETURNING *;
        $fn$;
        """
    )
    op.execute(f"ALTER FUNCTION {_SIG} OWNER TO app_admin;")
    op.execute(f"REVOKE ALL ON FUNCTION {_SIG} FROM PUBLIC;")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_SIG};")
