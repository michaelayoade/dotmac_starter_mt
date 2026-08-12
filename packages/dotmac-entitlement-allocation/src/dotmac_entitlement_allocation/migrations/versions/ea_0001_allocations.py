"""Allocation tables — the fourth module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and there
is **no `depends_on`**. That absence is the point rather than an omission: the
source implementation's `allocations.contract_id` carried a foreign key to
`contracts`, and reproducing it here would order this lineage behind a table
owned by a different module. Cross-module `depends_on` splices two
independently released lineages and makes either un-releasable without the
other.

`contract_ref` is therefore a bare UUID column with an index and no constraint.
An allocation is an immutable projection that must outlive the contract row it
was projected from.

## Platform catalog grants, not RLS

What a contract entitles is a vendor-side fact, not a per-tenant one — no
`tenant_id` to scope by, so RLS would have no predicate. Grants instead:

- `platform_api` — the ONLINE request-path role — SELECT and INSERT **only**.
  Same reasoning as `rl_0001`: an allocation is immutable, and immutability
  enforced by privilege survives a future router that forgets.
- `app_admin` — the OFFLINE migration role — full access, so a mis-staged
  allocation is correctable under review rather than not at all.
- `app_user` — the product data-plane role — REVOKEd. Ruling C4: the data plane
  is the only writer of its own `tenant_entitlement_grants` and learns what it
  may write from a signed envelope, never by reading the vendor's allocations.

## No CHECK on `capability_code`

The vocabulary belongs to the PRODUCTS, is manifest-derived, and differs per
product — a constraint here would need to be a different constraint per row.
Legality is proven at staging time against the caller-supplied catalogue, which
is where it can also be tested. `product_code` is stored beside it so the check
is reproducible: the codes were declared by THAT product's manifest.

Revision ID: ea_0001_allocations
Revises: (lineage root)
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ea_0001_allocations"
down_revision = None
branch_labels = ("entitlement_allocation",)
depends_on = None

# A literal, not `module_schema("ealloc")`. A migration is a frozen historical
# artifact, and the static gate reads this file without importing it.
_SCHEMA = "mod_ealloc"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_ealloc;")
    op.execute("GRANT USAGE ON SCHEMA mod_ealloc TO platform_api, app_admin;")

    op.create_table(
        "allocations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("contract_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("customer_ref", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_event_id", sa.String(length=200), nullable=False),
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
        sa.UniqueConstraint(
            "contract_ref",
            "content_hash",
            name="uq_allocations_contract_content",
        ),
        schema="mod_ealloc",
    )
    op.create_index(
        "ix_allocations_contract_ref",
        "allocations",
        ["contract_ref"],
        schema="mod_ealloc",
    )

    op.create_table(
        "allocation_entries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("allocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability_code", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["allocation_id"],
            ["mod_ealloc.allocations.id"],
            name="fk_allocation_entries_allocation_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "allocation_id",
            "capability_code",
            name="uq_allocation_entries_allocation_code",
        ),
        schema="mod_ealloc",
    )
    op.create_index(
        "ix_allocation_entries_allocation_id",
        "allocation_entries",
        ["allocation_id"],
        schema="mod_ealloc",
    )

    # Written out per table and per role rather than looped: these six lines are
    # the module's entire access-control surface and should be greppable.
    op.execute("GRANT SELECT, INSERT ON mod_ealloc.allocations TO platform_api;")
    op.execute("GRANT SELECT, INSERT ON mod_ealloc.allocation_entries TO platform_api;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ealloc.allocations TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_ealloc.allocation_entries "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_ealloc.allocations FROM app_user;")
    op.execute("REVOKE ALL ON mod_ealloc.allocation_entries FROM app_user;")


def downgrade() -> None:
    op.drop_index(
        "ix_allocation_entries_allocation_id",
        "allocation_entries",
        schema="mod_ealloc",
    )
    op.drop_table("allocation_entries", schema="mod_ealloc")
    op.drop_index("ix_allocations_contract_ref", "allocations", schema="mod_ealloc")
    op.drop_table("allocations", schema="mod_ealloc")
    op.execute("DROP SCHEMA IF EXISTS mod_ealloc RESTRICT;")
