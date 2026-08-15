"""Receipt delivery becomes claimable: a lease, a due time, and an outcome.

`inbox_receipts` recorded that a provider event arrived and was verified. It
could not record an attempt to DELIVER that observation to the product, so the
control plane could acknowledge a customer's message and then land it nowhere.

## Why these are columns on the receipt, not a delivery table

A delivery is a STATE OF THE RECEIPT. ADR-0014 gives at-most-once exactly one
owner, and the specific failure it records is a parallel ledger — "did this
land?" answered in two places with no tiebreak. A `receipt_deliveries` table
would be precisely that: two rows, two writers, and a reconciler needed to
decide which is true. `delivery_attempts` already exists for the OUTBOX and is
not reused here for the same reason in reverse — an inbound receipt and an
outbound dispatch have different identities, and sharing a table would make
"which side is this row about?" a column rather than a type.

## The lease is what makes the claim atomic

The claim is a CONDITIONAL UPDATE whose `rowcount == 1` IS the claim, exactly as
`execution.claim_delivery` already does for the outbox. `leased_until` is what
that predicate tests. It is a lease rather than a boolean `claimed` flag because
a worker that dies mid-call must release the receipt by the CLOCK — a flag needs
cleanup that, by construction, the dead worker is not going to run.

`next_attempt_at` puts the retry curve inside the same predicate, so a backed-off
receipt is passed over rather than claimed and immediately abandoned. Both are
in one index (`ix_inbox_receipts_due`) because they are always read together.

## Every column is nullable, and that is not laxness

A receipt exists, and is meaningful, before anything has tried to deliver it —
the recording of an observation is complete on its own. A NOT NULL default here
would have to invent a delivery state for every historical row and would make
"never attempted" indistinguishable from "attempted and reset".

## Provenance is copied, not joined

`destination_application`, `destination_contract_version` and
`destination_revision_id` duplicate what `capability_destination_revisions`
holds, deliberately. The live route can change after a delivery, and an incident
asking "where did THIS one go?" must get the answer for that delivery rather
than for whatever the route says today. `destination_revision_id` points at the
row in `capability_destination_revisions` (`ig_0004_destinations`) that was
current when the claim was taken — it is intentionally NOT a foreign key, so
that pruning route history can never delete delivery evidence.

## Plane

No new table, so `platform_tables` stays at eight and this module stays
platform-plane-only (ADR-0023). `app_user` holds nothing on `inbox_receipts`
(revoked in `ig_0001`), and a table-level REVOKE covers columns added later —
asserted at the column grain rather than assumed, because column privileges
exist only where an explicit per-column GRANT created one.

Revision ID: ig_0005_receipt_delivery
Revises: ig_0004_destinations
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0005_receipt_delivery"
down_revision = "ig_0004_destinations"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "inbox_receipts"

#: (name, type). Every one nullable — see the module docstring.
_COLUMNS: tuple[tuple[str, object], ...] = (
    ("leased_until", sa.DateTime(timezone=True)),
    ("next_attempt_at", sa.DateTime(timezone=True)),
    ("delivery_fingerprint", sa.String(length=64)),
    ("delivery_idempotency_key", sa.String(length=240)),
    ("correlation_id", sa.String(length=120)),
    ("product_acceptance", sa.String(length=32)),
    ("product_ref", sa.String(length=240)),
    ("destination_application", sa.String(length=160)),
    ("destination_contract_version", sa.Integer()),
    ("destination_revision_id", sa.Uuid()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column(
            _TABLE,
            sa.Column(name, type_, nullable=True),  # type: ignore[arg-type]
            schema=_SCHEMA,
        )

    # The claim predicate's index: state + due time + lease, read together on
    # every sweep.
    op.create_index(
        "ix_inbox_receipts_due",
        _TABLE,
        ["state", "next_attempt_at", "leased_until"],
        schema=_SCHEMA,
    )

    # No GRANT and no REVOKE. `app_user` holds nothing on this table (revoked in
    # `ig_0001`) and a table-level REVOKE covers columns added later, so
    # re-issuing it would read as load-bearing when it is not. The assumption is
    # asserted instead, at the column grain, in
    # `tests/test_integration_receipt_delivery_isolation.py`.


def downgrade() -> None:
    op.drop_index("ix_inbox_receipts_due", table_name=_TABLE, schema=_SCHEMA)
    for name, _ in reversed(_COLUMNS):
        op.drop_column(_TABLE, name, schema=_SCHEMA)
