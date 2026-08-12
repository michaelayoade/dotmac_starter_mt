"""Give the audit trail its real actor, and stop pretending it is a party.

Measured across ERP and Sub production: **93-98% of audit rows have a non-party
actor** — a scheduled job, a service principal, an API key. `actor_party_id`
alone was therefore NULL for almost every row it was supposed to identify. The
canonical actor becomes `(actor_type, actor_id)`; the party stays as optional
accountability enrichment, and `actor_label` as a write-time display snapshot
with no authority.

ERP independently built the same polymorphic pair, so the shape is a
two-product contract rather than one product's accommodation.

Two things this revision deliberately does NOT do:

* **No column gains a foreign key.** An audit row must stay readable after its
  actor is deleted. The kernel's `actor_party_id` was already non-FK; Sub's
  `actor_id` is non-FK for the same stated reason, and ERP's `actor_person_id`
  FK is the fleet's one divergence, to be dropped there rather than copied here.
* **No historical row is backfilled.** Rows written before this revision never
  recorded an actor kind, so `actor_type` stays NULL for them. Stamping them
  `system` would be inventing forensic data — the exact failure the write-side
  refuses at the API.

Every forensic column is nullable, `status_code` included. Its 100% population
in Sub is an artifact of a `default=200`, not evidence that every audit event is
an HTTP interaction: a reconciler or an internal transition has no status, and
NOT NULL would force each of them to invent a meaningless 200, which destroys
the field as a filter — "succeeded with 200" would stop being distinguishable
from "not a request at all".

Revision ID: 0023_audit_actor_and_forensics
Revises: 0022_party_role_grants
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0023_audit_actor_and_forensics"
down_revision = "0022_party_role_grants"
branch_labels = None
depends_on = None

_TABLE = "audit_events"

#: (name, type) for every column added here. All nullable, no defaults — see
#: `_set_occurred_at_default` for the one column that gets a default, and why it
#: takes a second statement to do it.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[Any]], ...] = (
    ("actor_type", sa.String(32)),
    ("actor_id", sa.String(120)),
    ("actor_label", sa.String(160)),
    ("request_id", sa.String(120)),
    ("status_code", sa.Integer()),
    ("is_success", sa.Boolean()),
    ("ip_address", sa.String(64)),
    ("user_agent", sa.String(255)),
    ("occurred_at", sa.DateTime(timezone=True)),
)

_INDEXED = ("actor_type", "actor_id", "actor_label", "request_id", "occurred_at")


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True))

    _set_occurred_at_default()

    for name in _INDEXED:
        op.create_index(f"ix_{_TABLE}_{name}", _TABLE, [name])


def _set_occurred_at_default() -> None:
    """Set the default in a SEPARATE statement from the ADD COLUMN.

    This is the whole reason `occurred_at` is added without a default above.

    `ADD COLUMN occurred_at timestamptz DEFAULT now()` does not rewrite the
    table on modern PostgreSQL — but it evaluates `now()` **once, at DDL time**
    and stores the result as the column's missing-value, so every pre-existing
    row reads back the migration's timestamp. That would assert, for the entire
    history of the table, that each event occurred the moment this migration
    ran: a falsehood applied at scale and indistinguishable afterwards from
    genuine data.

    Adding the column nullable and setting the default afterwards leaves
    historical rows NULL — correctly meaning "not recorded" — and gives the
    default only to rows inserted from here on.

    Rehearsed on PostgreSQL 16.14 against a table seeded with 1,000 historical
    rows, running both forms side by side:

        form             rows   historical NULL   stamped
        two-step (this)  1001              1000         1   (only the new insert)
        one statement    1000                 0      1000

    and the one-statement form produced exactly **one distinct timestamp**
    across all 1,000 rows — the moment the DDL ran. The split is load-bearing,
    not defensive style.
    """
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN occurred_at SET DEFAULT now()")


def downgrade() -> None:
    for name in _INDEXED:
        op.drop_index(f"ix_{_TABLE}_{name}", table_name=_TABLE)
    for name, _ in reversed(_COLUMNS):
        op.drop_column(_TABLE, name)
