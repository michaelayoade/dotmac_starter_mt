"""Session provenance — which external identity binding produced this session.

Closes the contract `dotmac_kernel.external_identity`'s module docstring has
carried as "deferred" since a63. It exists so that disabling a binding can
revoke exactly the sessions that binding produced, and nothing else.

Before this, disabling a binding stopped FURTHER logins but left every session
already issued from it working until it expired.

## `NULL` means ABSENT, and that is permanent

A password login has no binding, so most rows hold `NULL` forever. The column is
provenance that is ABSENT — never provenance that is unknown, and that
distinction decides the delete rule below.

## The FK carries `party_id`, and that is the point

The obvious FK is `(tenant_id, external_identity_binding_id)` →
`(tenant_id, id)`. It is not enough. It would permit a session for party A to
cite a binding belonging to party B — same tenant, wrong person — and nothing in
the schema would object. Selective revocation would then revoke B's sessions
while A's kept working, or worse, attribute A's session to B's identity in an
audit.

So the FK is `(tenant_id, party_id, external_identity_binding_id)` →
`(tenant_id, party_id, id)`. A session can only cite a binding that belongs to
the SAME party in the SAME tenant, and that is a database guarantee rather than
a service-layer convention. It needs a unique on
`external_identity_bindings (tenant_id, party_id, id)`, added here.

Two columns already on `auth_sessions` are reused rather than duplicated —
`tenant_id` and `party_id` are load-bearing in this constraint, not decoration.

## Why `ON DELETE RESTRICT` rather than `SET NULL` or `CASCADE`

`SET NULL` is the obvious choice and it is wrong: it would turn a session whose
provenance is KNOWN into one indistinguishable from a password session, quietly,
at exactly the moment somebody is discarding the binding — breaking the "absent,
never unknown" rule this column is defined by, and leaving the session LIVE while
erasing where it came from.

`CASCADE` is worse: deleting session rows destroys the record of who was signed
in, which is what becomes interesting after a takeover.

`RESTRICT` says what is true — a binding with sessions attached is not something
to delete casually. `disable_external_identity_binding` revokes them (setting
`revoked_at`, keeping the rows), and revoked rows still reference the binding, so
the ordinary path is unaffected.

## No RLS work

`auth_sessions` and `external_identity_bindings` both already have RLS ENABLEd
and FORCEd with tenant-isolation policies. Adding a column to an existing table
inherits them; hard rule 11's migration-time obligations were discharged when
each table was created.

Revision ID: 0025_session_provenance
Revises: 0024_external_identity_bindings
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_session_provenance"
down_revision = "0024_external_identity_bindings"
branch_labels = None
depends_on = None

_SESSIONS = "auth_sessions"
_BINDINGS = "external_identity_bindings"


def upgrade() -> None:
    # The FK target. `id` alone is the primary key, and a composite FK must
    # reference a unique over exactly the columns it names.
    op.create_unique_constraint(
        "uq_external_identity_bindings_tenant_party_id",
        _BINDINGS,
        ["tenant_id", "party_id", "id"],
    )

    op.add_column(
        _SESSIONS,
        sa.Column("external_identity_binding_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_auth_sessions_tenant_party_external_identity_binding",
        _SESSIONS,
        _BINDINGS,
        ["tenant_id", "party_id", "external_identity_binding_id"],
        ["tenant_id", "party_id", "id"],
        ondelete="RESTRICT",
    )
    # Revocation reads by (tenant, binding) inside a transaction that already
    # holds the binding's row lock — a human is waiting on this, not a sweeper.
    #
    # Partial, on NOT NULL only: the overwhelming majority of sessions are
    # password logins carrying NULL, and indexing those would cost write
    # throughput on every login to serve a query that never asks for them.
    op.create_index(
        "ix_auth_sessions_tenant_external_identity_binding",
        _SESSIONS,
        ["tenant_id", "external_identity_binding_id"],
        postgresql_where=sa.text("external_identity_binding_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_sessions_tenant_external_identity_binding", table_name=_SESSIONS
    )
    op.drop_constraint(
        "fk_auth_sessions_tenant_party_external_identity_binding",
        _SESSIONS,
        type_="foreignkey",
    )
    op.drop_column(_SESSIONS, "external_identity_binding_id")
    op.drop_constraint(
        "uq_external_identity_bindings_tenant_party_id", _BINDINGS, type_="unique"
    )
