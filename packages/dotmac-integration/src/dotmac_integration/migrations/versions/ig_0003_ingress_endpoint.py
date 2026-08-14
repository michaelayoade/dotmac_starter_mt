"""The minted ingress endpoint — one nullable, unique, rotatable address.

An ingress URL must address ONE stable identifier from which the module derives
installation, connector, capability, pinned manifest, active config revision
and secret references. A `(connector_key, capability_id)` pair cannot: several
installations may serve one pair (a production and a test provider account, or
two operators'), so the pair is ambiguous by construction.

## Why not `capability_bindings.id`

It is random, stable and derives the whole chain, and it was still rejected:

* **Minting.** With the primary key as the address, EVERY binding in the fleet
  implicitly carries a live ingress URL — delivery-only ones included. Since
  the PK is already interpolated into operator-facing error text and stored on
  receipt and delivery rows, anyone who has read an error log could then drive a
  connector's `verify`. With a minted key an unminted binding is a 404 that
  never reaches a plugin.
* **Rotation.** The PK is FK-referenced `ON DELETE CASCADE` from three tables,
  so changing it after a leak means rewriting the inbox history. And because
  the address lives in a third party's console, retrofitting a different
  identifier shape later costs a manual out-of-band reconfiguration per
  installation — the one migration nobody can run. So the rotatable shape is
  chosen on day one.

## No GRANT, no REVOKE, and that is asserted rather than assumed

Table-level privileges in Postgres cover columns added later, and column-level
privileges exist only where an explicit per-column GRANT created one. `app_user`
holds nothing on `mod_intg.capability_bindings` (revoked in `ig_0001`), so it
acquires nothing here. Re-issuing the REVOKE would read as load-bearing when it
is not; `tests/test_integration_isolation.py` checks the column privilege
directly instead.

No new table: the manifest's `platform_tables` stays at seven.

Revision ID: ig_0003_ingress_endpoint
Revises: ig_0002_execution
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0003_ingress_endpoint"
down_revision = "ig_0002_execution"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_BINDINGS = "capability_bindings"


def upgrade() -> None:
    op.add_column(
        _BINDINGS,
        sa.Column("ingress_endpoint_key", sa.String(64), nullable=True),
        schema=_SCHEMA,
    )
    # A unique INDEX, not a UniqueConstraint: Postgres treats NULLs as distinct
    # in a unique index, so every unminted binding coexists while a minted key
    # can never be shared.
    op.create_index(
        "uq_capability_bindings_ingress_endpoint",
        _BINDINGS,
        ["ingress_endpoint_key"],
        unique=True,
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_capability_bindings_ingress_endpoint",
        table_name=_BINDINGS,
        schema=_SCHEMA,
    )
    op.drop_column(_BINDINGS, "ingress_endpoint_key", schema=_SCHEMA)
