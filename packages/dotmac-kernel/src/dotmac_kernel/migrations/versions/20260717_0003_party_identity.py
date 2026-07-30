"""Party identity model replaces Person

Spec amendment 2026-07-17: `Person` is replaced by `Party` (`party_type`
person|organization) with subtype tables `party_persons`/`party_organizations`.
Auth credentials, RBAC grants, and audit actors rebind to `party_id`. The
starter has no production data — this is a destructive replace (template
repo), not a dual-write migration.

Drops the old `people`/`person_roles`-shaped tables (`people`, `person_roles`,
`user_credentials`, `auth_sessions` — the latter two recreated with `party_id`
FKs instead of `person_id`) and creates the new shape: `parties`,
`party_persons`, `party_organizations`, `party_roles`.

**Subtype-table RLS is different from every other tenant-scoped table**:
`party_persons` and `party_organizations` carry NO `tenant_id` column of
their own — they inherit tenant isolation entirely through the FK to
`parties`. Their RLS policies are `EXISTS`-based joins back to `parties`
instead of the usual `tenant_id = app_current_tenant_id()` comparison:

    EXISTS (
        SELECT 1 FROM parties p
        WHERE p.id = party_persons.party_id
          AND p.tenant_id = app_current_tenant_id()
    )

`parties` and `party_roles` (both of which DO carry `tenant_id`) keep the
standard tenant-comparison policy, same as every other tenant-scoped table.

Also renames `audit_events.actor_person_id` -> `actor_party_id` (in place —
`audit_events` itself is not dropped/recreated).

Revision ID: 0003_party_identity
Revises: 0002_settings_table
Create Date: 2026-07-17

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_party_identity"
down_revision = "0002_settings_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _drop_old_person_shaped_tables()
    _rename_audit_actor_column_forward()
    _create_parties_table()
    _create_party_persons_table()
    _create_party_organizations_table()
    _create_auth_tables()
    _create_party_roles_table()
    _apply_rls()
    _grant_roles()


def downgrade() -> None:
    _revoke_new_grants()
    _drop_new_rls()
    op.drop_table("party_roles")
    op.drop_table("auth_sessions")
    op.drop_table("user_credentials")
    op.drop_table("party_organizations")
    op.drop_table("party_persons")
    op.drop_index("uq_parties_tenant_lower_email", table_name="parties")
    op.drop_table("parties")
    _rename_audit_actor_column_backward()
    _recreate_old_person_shaped_tables()


# ─────────────────────────────────────────────────────────────────────────────
# Drop the old shape
# ─────────────────────────────────────────────────────────────────────────────


def _drop_old_person_shaped_tables() -> None:
    """Children before parent: auth_sessions/person_roles/user_credentials all
    FK to `people`. Dropping a table also drops its RLS policies — no separate
    `DROP POLICY` needed.
    """
    op.drop_table("auth_sessions")
    op.drop_table("person_roles")
    op.drop_table("user_credentials")
    op.drop_table("people")


def _recreate_old_person_shaped_tables() -> None:
    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "email", name="uq_people_tenant_email"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_people_tenant_id_id"),
    )
    op.create_index("ix_people_tenant_id", "people", ["tenant_id"])

    op.create_table(
        "user_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "email", name="uq_user_credentials_tenant_email"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            ondelete="CASCADE",
            name="fk_user_credentials_tenant_person",
        ),
    )
    op.create_index("ix_user_credentials_tenant_id", "user_credentials", ["tenant_id"])
    op.create_index("ix_user_credentials_person_id", "user_credentials", ["person_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "token_hash", name="uq_auth_sessions_tenant_token_hash"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_tenant_person",
        ),
    )
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index("ix_auth_sessions_person_id", "auth_sessions", ["person_id"])

    op.create_table(
        "person_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "person_id", "role_id", name="uq_person_roles_member"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            ondelete="CASCADE",
            name="fk_person_roles_tenant_person",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            ondelete="CASCADE",
            name="fk_person_roles_tenant_role",
        ),
    )
    op.create_index("ix_person_roles_tenant_id", "person_roles", ["tenant_id"])
    op.create_index("ix_person_roles_person_id", "person_roles", ["person_id"])
    op.create_index("ix_person_roles_role_id", "person_roles", ["role_id"])

    for table in ("people", "user_credentials", "auth_sessions", "person_roles"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (tenant_id = app_current_tenant_id())
                WITH CHECK (tenant_id = app_current_tenant_id());
            """
        )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "people, user_credentials, auth_sessions, person_roles TO app_user, platform_api;"
    )


# ─────────────────────────────────────────────────────────────────────────────
# audit_events.actor_person_id -> actor_party_id (in-place rename)
# ─────────────────────────────────────────────────────────────────────────────


def _rename_audit_actor_column_forward() -> None:
    op.alter_column("audit_events", "actor_person_id", new_column_name="actor_party_id")
    op.drop_index("ix_audit_events_actor_person_id", table_name="audit_events")
    op.create_index(
        "ix_audit_events_actor_party_id", "audit_events", ["actor_party_id"]
    )


def _rename_audit_actor_column_backward() -> None:
    op.drop_index("ix_audit_events_actor_party_id", table_name="audit_events")
    op.alter_column("audit_events", "actor_party_id", new_column_name="actor_person_id")
    op.create_index(
        "ix_audit_events_actor_person_id", "audit_events", ["actor_person_id"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# New shape: parties + subtype tables + auth/rbac rebinding
# ─────────────────────────────────────────────────────────────────────────────


def _create_parties_table() -> None:
    op.create_table(
        "parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("party_type", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "party_type IN ('person', 'organization')",
            name="ck_parties_party_type",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_parties_tenant_id_id"),
    )
    op.create_index("ix_parties_tenant_id", "parties", ["tenant_id"])
    # Case-insensitive: two rows in the same tenant can't share an email
    # regardless of casing. Partial (WHERE email IS NOT NULL): organization
    # parties commonly have no email, and NULL never collides in a unique
    # index anyway, but the partial index keeps intent explicit and the
    # index smaller.
    op.create_index(
        "uq_parties_tenant_lower_email",
        "parties",
        ["tenant_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def _create_party_persons_table() -> None:
    """No `tenant_id` — isolation inherited via FK to `parties` (see module
    docstring for the RLS policy this requires).
    """
    op.create_table(
        "party_persons",
        sa.Column(
            "party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
    )


def _create_party_organizations_table() -> None:
    """No `tenant_id` — isolation inherited via FK to `parties` (see module
    docstring for the RLS policy this requires).
    """
    op.create_table(
        "party_organizations",
        sa.Column(
            "party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("legal_name", sa.String(200), nullable=False),
    )


def _create_auth_tables() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "email", name="uq_user_credentials_tenant_email"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_user_credentials_tenant_party",
        ),
    )
    op.create_index("ix_user_credentials_tenant_id", "user_credentials", ["tenant_id"])
    op.create_index("ix_user_credentials_party_id", "user_credentials", ["party_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "token_hash", name="uq_auth_sessions_tenant_token_hash"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_tenant_party",
        ),
    )
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index("ix_auth_sessions_party_id", "auth_sessions", ["party_id"])


def _create_party_roles_table() -> None:
    op.create_table(
        "party_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "party_id", "role_id", name="uq_party_roles_member"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_party",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_role",
        ),
    )
    op.create_index("ix_party_roles_tenant_id", "party_roles", ["tenant_id"])
    op.create_index("ix_party_roles_party_id", "party_roles", ["party_id"])
    op.create_index("ix_party_roles_role_id", "party_roles", ["role_id"])


# ─────────────────────────────────────────────────────────────────────────────
# RLS
# ─────────────────────────────────────────────────────────────────────────────

_STANDARD_TENANT_TABLES = (
    "parties",
    "user_credentials",
    "auth_sessions",
    "party_roles",
)
_SUBTYPE_TABLES = ("party_persons", "party_organizations")


def _apply_rls() -> None:
    for table in _STANDARD_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (tenant_id = app_current_tenant_id())
                WITH CHECK (tenant_id = app_current_tenant_id());
            """
        )

    # Subtype tables carry no tenant_id — isolation is an EXISTS join back to
    # parties (see module docstring).
    for table in _SUBTYPE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        # `table` is always one of the two hardcoded literals in
        # _SUBTYPE_TABLES above, never external input.
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (
                    EXISTS (
                        SELECT 1 FROM parties p
                        WHERE p.id = {table}.party_id
                          AND p.tenant_id = app_current_tenant_id()
                    )
                )
                WITH CHECK (
                    EXISTS (
                        SELECT 1 FROM parties p
                        WHERE p.id = {table}.party_id
                          AND p.tenant_id = app_current_tenant_id()
                    )
                );
            """  # noqa: S608
        )


def _drop_new_rls() -> None:
    for table in (*_STANDARD_TENANT_TABLES, *_SUBTYPE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")


# ─────────────────────────────────────────────────────────────────────────────
# Grants
# ─────────────────────────────────────────────────────────────────────────────


def _grant_roles() -> None:
    """Mirrors the phase-1 migration's grants for people/person_roles."""
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "parties, party_persons, party_organizations, "
        "user_credentials, auth_sessions, party_roles "
        "TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "parties, party_persons, party_organizations, "
        "user_credentials, auth_sessions, party_roles "
        "TO platform_api;"
    )


def _revoke_new_grants() -> None:
    op.execute(
        "REVOKE ALL ON "
        "parties, party_persons, party_organizations, "
        "user_credentials, auth_sessions, party_roles "
        "FROM app_user, platform_api;"
    )
