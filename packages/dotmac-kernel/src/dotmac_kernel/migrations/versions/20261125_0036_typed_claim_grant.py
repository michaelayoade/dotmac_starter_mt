"""POLICY CHANGE: EXECUTE on the typed claim for `outbox_dispatcher`.

======================  FOR MICHAEL TO APPROVE AT MERGE  ======================
This revision is the ONLY new privilege grant in this change. It lets the
cross-tenant `outbox_dispatcher` role EXECUTE
`public.claim_outbox_batch_by_type(text, integer, integer, text[])`.

It is NARROWER than what the role holds today. The dispatcher can already
EXECUTE `claim_outbox_batch`, which claims every event type; the typed claim
claims a subset of those same rows (same predicate plus an event-type filter) and
returns the same columns. No table or column privilege is granted, the role stays
NOSUPERUSER and NOBYPASSRLS, and PUBLIC still has no EXECUTE.
===============================================================================

It is split from `0035_outbox_typed_claim` so the grant can be reviewed, held or
reverted on its own.

Revision ID: 0036_typed_claim_grant
Revises: 0035_outbox_typed_claim
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op

revision = "0036_typed_claim_grant"
down_revision = "0035_outbox_typed_claim"
branch_labels = None
depends_on = None

_SIG = "public.claim_outbox_batch_by_type(text, integer, integer, text[])"


def upgrade() -> None:
    # POLICY HUNK: the single new grant to outbox_dispatcher.
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIG} TO outbox_dispatcher;")


def downgrade() -> None:
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_SIG} FROM outbox_dispatcher;")
