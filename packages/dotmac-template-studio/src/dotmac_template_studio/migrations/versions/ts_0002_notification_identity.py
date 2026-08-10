"""Re-base templates on the notification contract (ADR-0006 § 5b).

The 2026-08-10 Template Studio source audit disqualified this module's two-kind
merge. `kind` is dropped, `channel` becomes identity-bearing and NOT NULL, and a
`context` column names the registered `RenderContext` whose vocabulary a
template's revisions are validated against.

## Why a second revision rather than amending `ts_0001`

`ts_0001` has been applied to development databases. A released migration is a
frozen historical artifact: editing one in place means two databases that both
claim revision `ts_0001_templates` have different shapes, which is exactly the
drift the lineage exists to prevent. The module has no product consumer yet, so
this revision may be blunt about data — see the backfill note below — but it may
not be dishonest about history.

## The `channel` backfill

`channel` was nullable and is becoming part of a unique key. Existing rows are
development data only (no product has cut over — see `EXTRACTION.toml`), so a
NULL is filled with `'email'` rather than carrying a nullable identity column
forever. If this module ever ships to a consumer before this revision does, that
default must become a real per-row decision instead.

Everything is fully qualified to `mod_tstudio`: every `op.*` call carries
`schema=`, every raw statement names the schema. `search_path` is connection
state a pooler or another module can change.

Revision ID: ts_0002_notify_identity
Revises: ts_0001_templates
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ts_0002_notify_identity"
down_revision = "ts_0001_templates"
branch_labels = None
depends_on = None

# A literal, not `module_schema("tstudio")` — a migration must keep building the
# same schema even if a future kernel changed how a name is derived, and the
# static gate reads this file without importing it.
_SCHEMA = "mod_tstudio"

_TEMPLATES = "templates"


def upgrade() -> None:
    # 1. The new `context` column. Added nullable, backfilled, then made NOT
    #    NULL — the three-step shape that works on a table with existing rows.
    op.add_column(
        _TEMPLATES,
        sa.Column("context", sa.String(60), nullable=True),
        schema=_SCHEMA,
    )
    # Development rows predate render contexts entirely; `default` is the
    # context the reference assembly registers.
    op.execute(
        "UPDATE mod_tstudio.templates SET context = 'default' WHERE context IS NULL;"
    )
    op.alter_column(
        _TEMPLATES,
        "context",
        existing_type=sa.String(60),
        nullable=False,
        schema=_SCHEMA,
    )

    # 2. `channel` joins the identity, so it can no longer be NULL.
    op.execute(
        "UPDATE mod_tstudio.templates SET channel = 'email' WHERE channel IS NULL;"
    )
    op.alter_column(
        _TEMPLATES,
        "channel",
        existing_type=sa.String(20),
        nullable=False,
        schema=_SCHEMA,
    )

    # 3. Swap the identity constraint. Dropped before the column it names, and
    #    the CHECK goes with the discriminator it constrained.
    op.drop_constraint(
        "uq_templates_tenant_slug", _TEMPLATES, type_="unique", schema=_SCHEMA
    )
    op.drop_constraint("ck_templates_kind", _TEMPLATES, type_="check", schema=_SCHEMA)
    op.drop_column(_TEMPLATES, "kind", schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_templates_tenant_slug_channel",
        _TEMPLATES,
        ["tenant_id", "slug", "channel"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_templates_tenant_slug_channel", _TEMPLATES, type_="unique", schema=_SCHEMA
    )
    op.add_column(
        _TEMPLATES,
        sa.Column("kind", sa.String(20), nullable=True),
        schema=_SCHEMA,
    )
    op.execute(
        "UPDATE mod_tstudio.templates SET kind = 'notification' WHERE kind IS NULL;"
    )
    op.alter_column(
        _TEMPLATES,
        "kind",
        existing_type=sa.String(20),
        nullable=False,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_templates_kind",
        _TEMPLATES,
        "kind IN ('notification', 'document')",
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_templates_tenant_slug",
        _TEMPLATES,
        ["tenant_id", "kind", "slug"],
        schema=_SCHEMA,
    )
    op.alter_column(
        _TEMPLATES,
        "channel",
        existing_type=sa.String(20),
        nullable=True,
        schema=_SCHEMA,
    )
    op.drop_column(_TEMPLATES, "context", schema=_SCHEMA)
