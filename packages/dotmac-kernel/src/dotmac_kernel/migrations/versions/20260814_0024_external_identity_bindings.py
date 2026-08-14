"""External identity bindings — a verified external subject to one local Party.

Creates `external_identity_bindings`, the local half of federated login. The
protocol half lives outside the kernel entirely; this table answers only *which
party is this subject, here*.

Ported from ERP's `federated_identities` (`issuer`, `subject` → `person_id`,
`is_active`, `last_authenticated_at`) with Sub's binding discriminator and
evidence pair. Three port deltas, all of them things neither source has:

1. **`tenant_id` and RLS.** ERP's table has no tenant or organization column at
   all, so its `(issuer, subject)` uniqueness is GLOBAL across every ERP
   organization and the boundary is enforced only transitively through the
   person FK. Sub's migration 527 states `- no NOT NULL, no RLS` outright and
   its tests assert the absence. Neither is acceptable for a table that decides
   who a login is: a cross-tenant read enumerates another tenant's workforce
   against an IdP, and a cross-tenant write binds an attacker's subject to
   somebody else's party. Hard rule 11 in one migration — `tenant_id NOT NULL`,
   composite uniques that include it, RLS ENABLEd *and* FORCEd, an isolation
   policy, and the online-role grants. FORCE matters: without it the table
   owner (which migrations run as) bypasses its own policy.

2. **`provider_binding`.** ERP echoes one globally-configured issuer, so it
   cannot express two providers; its schema cannot say which registration
   authenticated a subject. Sub's `authentication_bindings.binding_key` is that
   concept, and its design note is the reason it discriminates: *"two OIDC
   issuers or two RADIUS verifiers are two bindings of one code."* Plain string,
   NOT a declared vocabulary — an operator configures a provider, and ADR-0026
   §4 draws exactly this line for `policy_code`.

3. **`bound_by` / `bind_reason`.** A binding grants a login, so who created it
   and why belong in the row. Sub CHECK-forces the equivalent pair non-blank;
   ERP records neither.

CHECK constraints rather than trusting the service layer: the service is the
only writer TODAY, and a NOT NULL that permits `''` is not a constraint.

Revision ID: 0024_external_identity_bindings
Revises: 0023_audit_actor_and_forensics
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_external_identity_bindings"
down_revision = "0023_audit_actor_and_forensics"
branch_labels = None
depends_on = None

_TABLE = "external_identity_bindings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        sa.Column("provider_binding", sa.String(80), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("bound_by", sa.String(120), nullable=False),
        sa.Column("bind_reason", sa.String(500), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_external_identity_bindings_tenant",
        ),
        # Composite, so the FK itself cannot span tenants — the same shape
        # `user_credentials` and `auth_sessions` use.
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_external_identity_bindings_tenant_party",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_binding",
            "issuer",
            "subject",
            name="uq_external_identity_bindings_tenant_provider_subject",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_binding",
            "party_id",
            name="uq_external_identity_bindings_tenant_provider_party",
        ),
        sa.CheckConstraint(
            "length(trim(provider_binding)) > 0",
            name="ck_external_identity_bindings_provider_binding_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(issuer)) > 0",
            name="ck_external_identity_bindings_issuer_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(subject)) > 0",
            name="ck_external_identity_bindings_subject_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(bound_by)) > 0",
            name="ck_external_identity_bindings_bound_by_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(bind_reason)) > 0",
            name="ck_external_identity_bindings_bind_reason_nonempty",
        ),
    )
    op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])
    op.create_index(f"ix_{_TABLE}_party_id", _TABLE, ["party_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index(f"ix_{_TABLE}_party_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
