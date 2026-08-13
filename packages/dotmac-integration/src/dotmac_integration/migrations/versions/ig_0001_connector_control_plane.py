"""Integration's schema and control-plane tables — a PLATFORM-ONLY lineage.

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and
`depends_on` (never `down_revision`) orders this after the kernel's base
lineage. Cross-lineage ordering is `depends_on` by rule — a `down_revision`
across owners would splice two independently released lineages into one chain
and make either un-releasable.

Everything is fully qualified to `mod_intg`.

## Every table here is PLATFORM plane (ADR-0023)

No `tenant_id`, no RLS, GRANT to `platform_api`/`app_admin`, and **REVOKE ALL
from `app_user`**. The revoke is the load-bearing half: on this plane the
privilege boundary IS the isolation, and the kernel's live-catalog gate checks
it as strictly as it checks an RLS policy on the tenant side.

That is not an omission of tenancy — it is the correct scope. A connector
installation is a control-plane fact about the fleet's integrations; no product
queries it, and it belongs to no tenant. The source agrees: none of
`dotmac_sub`'s seven integration tables carries a `tenant_id`.

## Configuration revisions are immutable

`connector_config_revisions` is insert-only by design, and `config_digest` is
what makes that checkable. Immutability is not enforced by privilege here the
way `dotmac-release-catalog` does it, because the control plane must be able to
mark a revision's validation outcome after the fact; the digest is what proves
the CONFIG did not move.

## No CHECK on `capability_id`

Capability ids are an open, contract-versioned vocabulary (`ticket.observation.v1`),
declared by independently released connectors. A CHECK constraint would need an
`ALTER TABLE` every time a connector shipped a new contract — the exact growth
problem ADR-0008 records against native enums. It is validated in
`dotmac_integration.spi`, where it is also testable.

Revision ID: ig_0001_connector_cp
Revises: (lineage root)
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0001_connector_cp"
down_revision = None
branch_labels = ("integration",)
depends_on = ("0001_initial_tenant_schema",)

# A literal, not `module_schema("intg")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_intg"

_INSTALLATIONS = "connector_installations"
_REVISIONS = "connector_config_revisions"
_BINDINGS = "capability_bindings"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_intg;")
    # `app_user` gets USAGE but no table privilege below: the schema being
    # reachable is not the same as its tables being readable, and revoking at
    # the table is what the gate checks.
    op.execute("GRANT USAGE ON SCHEMA mod_intg TO platform_api, app_admin;")

    op.create_table(
        _INSTALLATIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_key", sa.String(120), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("spi_range", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column(
            "environment", sa.String(24), nullable=False, server_default="production"
        ),
        sa.Column("state", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("current_config_revision_id", sa.Uuid(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=True),
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
            "connector_key", "name", name="uq_connector_installations_key_name"
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'validating', 'enabled', 'disabled', "
            "'quarantined', 'retired')",
            name="ck_connector_installations_state",
        ),
        sa.CheckConstraint(
            "environment IN ('production', 'sandbox', 'test')",
            name="ck_connector_installations_environment",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_connector_installations_key_state",
        _INSTALLATIONS,
        ["connector_key", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        _REVISIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("config_json", _JSON, nullable=False),
        sa.Column("secret_refs", _JSON, nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column(
            "validation_status", sa.String(24), nullable=False, server_default="pending"
        ),
        sa.Column("validation_errors", _JSON, nullable=True),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["mod_intg.connector_installations.id"],
            ondelete="CASCADE",
            name="fk_connector_config_revisions_installation",
        ),
        sa.UniqueConstraint(
            "installation_id", "revision", name="uq_connector_config_revisions_number"
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'valid', 'invalid')",
            name="ck_connector_config_revisions_validation",
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_connector_config_revisions_revision"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_connector_config_revisions_installation",
        _REVISIONS,
        ["installation_id", "revision"],
        schema=_SCHEMA,
    )

    op.create_table(
        _BINDINGS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="disabled"),
        sa.Column("scope_json", _JSON, nullable=True),
        sa.Column("policy_json", _JSON, nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("updated_by", sa.String(160), nullable=True),
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
            ["installation_id"],
            ["mod_intg.connector_installations.id"],
            ondelete="CASCADE",
            name="fk_capability_bindings_installation",
        ),
        # ADR-0024 § 7's tuple: an installation binds a capability ONCE.
        # `capability_id` alone is deliberately NOT unique — many installations
        # may implement one capability, and choosing between them is a dispatch
        # decision (`dotmac_integration.selection`), not a schema one.
        sa.UniqueConstraint(
            "installation_id",
            "capability_id",
            name="uq_capability_bindings_installation_capability",
        ),
        sa.CheckConstraint(
            "state IN ('disabled', 'enabled')", name="ck_capability_bindings_state"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_capability_bindings_capability_state",
        _BINDINGS,
        ["capability_id", "state"],
        schema=_SCHEMA,
    )

    # Literal per table, never looped: the composed gate reads this file
    # statically without importing it, so a statement built from a loop variable
    # is uninspectable and fails closed — correctly.
    #
    # The REVOKEs come last so a future edit adding a grant cannot silently
    # outrank them. On this plane the revoke IS the isolation.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.connector_installations "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.connector_installations "
        "TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.connector_config_revisions TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.connector_config_revisions TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.capability_bindings "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.capability_bindings "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.connector_installations FROM app_user;")
    op.execute("REVOKE ALL ON mod_intg.connector_config_revisions FROM app_user;")
    op.execute("REVOKE ALL ON mod_intg.capability_bindings FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.capability_bindings CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_intg.connector_config_revisions CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_intg.connector_installations CASCADE;")
    # The schema itself is left in place: removing a namespace is a deliberate
    # operator act, and a CASCADE here could take an unrelated owner's object.
