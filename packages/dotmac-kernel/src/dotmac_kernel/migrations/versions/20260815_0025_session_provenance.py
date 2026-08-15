"""Session provenance — which external identity binding produced this session.

Closes the contract `dotmac_kernel.external_identity`'s module docstring has
carried since a63 as "deferred", point by point. It exists so that disabling a
binding can revoke exactly the sessions that binding produced, and nothing else.

Before this, disabling a binding stopped FURTHER logins (the `UPDATE` takes the
same row lock `finalize_external_login` holds) but left every session already
issued from it working until it expired. That gap was named in the docstring
rather than hidden, and this is the migration half of closing it.

## `NULL` means ABSENT, and that is permanent

A password login has no binding, so most rows will hold `NULL` forever. The
column is provenance that is ABSENT — never provenance that is unknown, and the
distinction decides the FK's delete rule below.

## Why `ON DELETE RESTRICT` rather than `SET NULL` or `CASCADE`

`SET NULL` is the obvious choice and it is wrong here: it would turn a session
whose provenance is KNOWN into one indistinguishable from a password session,
silently, at exactly the moment somebody is discarding the binding. That breaks
the "absent, never unknown" rule this column is defined by, and it leaves the
session LIVE while erasing the evidence of where it came from.

`CASCADE` is worse — deleting session rows destroys the audit trail of who was
signed in, which is the record that becomes interesting after a takeover.

`RESTRICT` says what is actually true: a binding with sessions attached cannot
just be deleted. `disable_external_identity_binding` revokes them (setting
`revoked_at`, keeping the rows), so the ordinary path is unaffected — revoked
rows still reference the binding and still block a delete. Deleting a binding is
already described as an exceptional, evidence-discarding act (see
`external_identity.py` on reassignment); making it also confront the sessions it
issued is the honest cost of that act, not an obstacle to routine work.

## The composite FK, and the unique it needs

`(tenant_id, external_identity_binding_id)` → `(tenant_id, id)`, the same shape
`auth_sessions` already uses for `party_id`: a two-column FK cannot span
tenants, so a session in tenant A can never cite a binding in tenant B even if
somebody writes the id in by hand.

PostgreSQL requires the referenced columns to carry a unique constraint, and
`external_identity_bindings` has uniques on
`(tenant_id, provider_binding, subject)` and `(tenant_id, provider_binding,
party_id)` but none on `(tenant_id, id)` — `id` alone is the primary key. So
this migration adds `uq_external_identity_bindings_tenant_id_id` first, exactly
as `parties` and `roles` carry theirs for the same reason.

## No RLS work

`auth_sessions` and `external_identity_bindings` both already have RLS ENABLEd
and FORCEd with tenant-isolation policies. A new column on an existing table
inherits them; adding a column is not adding a table, and hard rule 11's
migration-time obligations were discharged when each table was created.

Revision ID: 0025_session_provenance
Revises: 0024_external_identity_bindings
Create Date: 2026-08-15
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
    # The FK target. `id` is the PK, but a composite FK needs a unique over
    # exactly the referenced pair — see the module docstring.
    op.create_unique_constraint(
        "uq_external_identity_bindings_tenant_id_id",
        _BINDINGS,
        ["tenant_id", "id"],
    )

    op.add_column(
        _SESSIONS,
        sa.Column("external_identity_binding_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_auth_sessions_tenant_external_identity_binding",
        _SESSIONS,
        _BINDINGS,
        ["tenant_id", "external_identity_binding_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    # Revocation reads by binding, and it runs inside the same transaction as a
    # disable that already holds the binding's row lock — so this index is on
    # the path a human is waiting for, not a background sweep.
    #
    # Partial, on NOT NULL only: the overwhelming majority of sessions are
    # password logins carrying NULL, and indexing those costs write throughput
    # on every login to serve a query that never asks for them.
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
        "fk_auth_sessions_tenant_external_identity_binding",
        _SESSIONS,
        type_="foreignkey",
    )
    op.drop_column(_SESSIONS, "external_identity_binding_id")
    op.drop_constraint(
        "uq_external_identity_bindings_tenant_id_id", _BINDINGS, type_="unique"
    )
