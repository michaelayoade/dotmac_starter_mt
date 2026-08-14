"""Create the explicit tenant and platform approval planes (ADR-0023, ADR-0026).

## What this lineage needs

An approval needs a tenant to hang a foreign key on and roles to grant to.
Nothing else. Two logical prerequisites, never a physical edge to a foreign
revision:

- `tenant_scope_catalog.v1` — the FK target `public.tenants.id` and the
  `public.app_current_tenant_id()` the RLS policies evaluate;
- `module_database_roles.v1` — `app_user`, `platform_api` and `app_admin`.

Deliberately NOT an identity or RBAC prerequisite. ERP's source service joined
`PersonRole`/`Role` directly to decide eligibility; this module takes role
membership as a value at the call site instead, so it installs beside a product
whose RBAC estate the kernel has never seen.

## The two planes, and why the grants differ

Tenant tables carry `tenant_id NOT NULL`, a composite tenant identity, and
FORCEd row-level security — the isolation is the policy (hard rule 11).

Platform tables carry no tenant column and no RLS, and are REVOKEd from
`app_user` across every privilege — there, the revocation IS the isolation
(ADR-0023). `platform_api` and `app_admin` keep row DML so the online control
plane can operate.

No foreign key crosses between the planes, and none points into an adopting
product's schema: `subject_id` is an opaque string reference, not a relation.

Revision ID: ap_0001_approvals
Revises: (lineage root)
Create Date: 2026-08-14
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import all_bound, resolve_depends_on

from alembic import op

revision = "ap_0001_approvals"
down_revision = None
branch_labels = ("approvals",)

# Literals, not imported constants: a migration is a snapshot of an accepted
# decision, and the composed gate reads this list statically to diff it against
# `dotmac_approvals.manifest`.
# Split by PLANE (ADR-0027), not merged into one list. The platform plane needs
# roles to grant to and nothing else; the tenant plane additionally needs a
# tenant catalogue to point a foreign key at and an RLS predicate to evaluate.
#
# That split is what makes this module installable in the vendor control plane,
# which has no tenant catalogue and never will — it is not a product data plane.
# Under one merged list the lineage demanded a tenant scope in order to create
# ANY table, so a platform-only assembly could not install it at all.
PLATFORM_REQUIRES = ("module_database_roles.v1",)
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
REQUIRES = PLATFORM_REQUIRES + TENANT_REQUIRES

# A bound optional prerequisite still contributes a real ordering edge: where
# the tenant plane IS built, it must still run after whatever supplies the
# catalogue. Where it is not, the edge is simply absent.
depends_on = resolve_depends_on(PLATFORM_REQUIRES, optional=TENANT_REQUIRES)

_SCHEMA = "mod_approvals"

_DIGEST = sa.String(71)
_CODE = sa.String(120)


def _policy_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("policy_code", _CODE, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("levels", sa.JSON(), nullable=False),
        sa.Column(
            "allow_self_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("document_digest", _DIGEST, nullable=False),
    ]


def _request_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("policy_code", _CODE, nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("subject_type", _CODE, nullable=False),
        sa.Column("subject_id", sa.String(200), nullable=False),
        sa.Column("content_digest", _DIGEST, nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("current_level", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    ]


def _decision_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("delegated_from", sa.Uuid(), nullable=True),
        sa.Column(
            "mfa_verified", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _timestamps() -> list[sa.Column[Any]]:
    return [
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
    ]


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE", name=name
    )


def upgrade() -> None:
    # Before any DDL: the binding is a claim, so check it against the database.
    require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    tenant_plane = all_bound(TENANT_REQUIRES)
    if tenant_plane:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_approvals;")
    op.execute("GRANT USAGE ON SCHEMA mod_approvals TO platform_api, app_admin;")
    if tenant_plane:
        # Only when there is something here for the tenant role to reach. A
        # platform-only schema must not demand tenant-role USAGE (kernel
        # 0.1.0a57).
        op.execute("GRANT USAGE ON SCHEMA mod_approvals TO app_user;")

    _upgrade_platform_plane()
    if tenant_plane:
        _upgrade_tenant_plane()


def _upgrade_tenant_plane() -> None:
    """Built only where the assembly bound `tenant_scope_catalog.v1`."""
    op.create_table(
        "approval_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_policy_columns(),
        *_timestamps(),
        _tenant_fk("fk_approval_policies_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_approval_policies_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_code",
            "version",
            name="uq_approval_policies_tenant_code_version",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_approval_policies_tenant_code",
        "approval_policies",
        ["tenant_id", "policy_code"],
        schema=_SCHEMA,
    )

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_request_columns(),
        *_timestamps(),
        _tenant_fk("fk_approval_requests_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_approval_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_approval_requests_tenant_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_approval_requests_tenant_subject",
        "approval_requests",
        ["tenant_id", "subject_type", "subject_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_approval_requests_tenant_state",
        "approval_requests",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        *_decision_columns(),
        *_timestamps(),
        _tenant_fk("fk_approval_decisions_tenant"),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mod_approvals.approval_requests.id"],
            ondelete="CASCADE",
            name="fk_approval_decisions_request",
        ),
        # One actor, one vote per level — the DURABLE half of distinct-actor
        # quorum. The in-memory check refuses politely; this is what holds when
        # two approvals race.
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "level",
            "actor_id",
            name="uq_approval_decisions_one_vote",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_approval_decisions_tenant_request",
        "approval_decisions",
        ["tenant_id", "request_id"],
        schema=_SCHEMA,
    )

    for table in ("approval_policies", "approval_requests", "approval_decisions"):
        op.execute(f"ALTER TABLE mod_approvals.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_approvals.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
                ON mod_approvals.{table}
                USING (tenant_id = public.app_current_tenant_id())
                WITH CHECK (tenant_id = public.app_current_tenant_id());
            """
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_approvals.{table} TO app_user;"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_approvals.{table} TO platform_api;"
        )


def _upgrade_platform_plane() -> None:
    """Always built: the control plane is the one both assemblies can operate."""
    op.create_table(
        "platform_approval_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_policy_columns(),
        *_timestamps(),
        sa.UniqueConstraint(
            "policy_code",
            "version",
            name="uq_platform_approval_policies_code_version",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_approval_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_request_columns(),
        *_timestamps(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_approval_requests_idempotency"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_approval_requests_subject",
        "platform_approval_requests",
        ["subject_type", "subject_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_approval_requests_state",
        "platform_approval_requests",
        ["state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "platform_approval_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        *_decision_columns(),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mod_approvals.platform_approval_requests.id"],
            ondelete="CASCADE",
            name="fk_platform_approval_decisions_request",
        ),
        sa.UniqueConstraint(
            "request_id",
            "level",
            "actor_id",
            name="uq_platform_approval_decisions_one_vote",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_approval_decisions_request",
        "platform_approval_decisions",
        ["request_id"],
        schema=_SCHEMA,
    )

    for table in (
        "platform_approval_policies",
        "platform_approval_requests",
        "platform_approval_decisions",
    ):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_approvals.{table} TO platform_api;"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_approvals.{table} TO app_admin;"
        )
        # The revocation IS the isolation on this plane.
        op.execute(f"REVOKE ALL ON mod_approvals.{table} FROM app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS mod_approvals.platform_approval_decisions CASCADE;"
    )
    op.execute("DROP TABLE IF EXISTS mod_approvals.platform_approval_requests CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_approvals.platform_approval_policies CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_approvals.approval_decisions CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_approvals.approval_requests CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_approvals.approval_policies CASCADE;")
