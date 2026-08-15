"""The destination gets its own append-only table.

A destination decides which application receives a stream. It was previously a
block inside `connector_config_revisions.config_json`, which gave a routing
authority the same lifecycle, the same writers and the same review surface as
an endpoint URL or a timeout. Three consequences, all of them avoidable:

* **One blob, two authorities.** Editing a connector's tuning and redirecting a
  customer's messages were the same operation on the same immutable object.
  Separation is not tidiness here — it is what lets the two be granted,
  audited and reviewed independently.
* **History that had to be reconstructed.** "What was this routed to on the
  3rd?" meant diffing JSON across config revisions and hoping the destination
  block had not been reformatted. It is now a row.
* **A shape no constraint could hold.** `{"application": ..., "scope": {...}}`
  inside JSON cannot be NOT NULL, cannot be length-bounded and cannot be
  indexed. A malformed scope surfaced at resolution time, on a live delivery,
  as a refusal — rather than at write time, in front of the operator who made
  the mistake.

## Append-only, and why the current row is `MAX(revision)`

The same rule `connector_config_revisions` already follows: inserted, never
updated. There is deliberately no `is_current` flag and no pointer column on
`capability_bindings`. Both are denormalizations that need a writer to keep
them true, and a routing pointer that can drift is the exact failure this table
exists to make impossible. `MAX(revision)` cannot disagree with the history
because it IS the history.

No backfill. The destination block was read by `resolve_destination` and
written by nothing — the module shipped without an establishment path, so there
is no production row to carry over, and a migration inventing routes from
whatever JSON happened to be present would be manufacturing routing decisions
nobody made. Deployments establish destinations explicitly through
`establish_destination`, which applies the declared-owner check.

## Plane

Platform, like every other table in this module: no `tenant_id`, no RLS, and
`REVOKE ALL FROM app_user` — which is the isolation on this plane (ADR-0023).
The manifest's `platform_tables` goes from seven to eight; `tables` stays
empty.

Revision ID: ig_0004_destinations
Revises: ig_0003_ingress_endpoint
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0004_destinations"
down_revision = "ig_0003_ingress_endpoint"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "capability_destination_revisions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("application", sa.String(length=160), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column("scope_ref", sa.String(length=320), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("established_by", sa.String(length=160), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["capability_binding_id"],
            [f"{_SCHEMA}.capability_bindings.id"],
            ondelete="CASCADE",
            name="fk_capability_destination_revisions_binding",
        ),
        sa.UniqueConstraint(
            "capability_binding_id",
            "revision",
            name="uq_capability_destination_revisions_number",
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_capability_destination_revisions_revision"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_capability_destination_revisions_current",
        _TABLE,
        ["capability_binding_id", "revision"],
        schema=_SCHEMA,
    )

    # Grants last, then the revoke — the same order as `ig_0001`, so an edit
    # that adds a grant below the revoke reads as the mistake it would be.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{_TABLE} TO platform_api;"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{_TABLE} TO app_admin;"
    )
    # The load-bearing half. The tenant application role reaches no row here,
    # and because this is a table-level REVOKE it also covers every column
    # added to this table later.
    op.execute(f"REVOKE ALL ON {_SCHEMA}.{_TABLE} FROM app_user;")


def downgrade() -> None:
    op.drop_index(
        "ix_capability_destination_revisions_current", table_name=_TABLE, schema=_SCHEMA
    )
    op.drop_table(_TABLE, schema=_SCHEMA)
