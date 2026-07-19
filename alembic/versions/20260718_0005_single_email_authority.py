"""single email authority

Phase 2b.1 Task 3 (finding F2): `user_credentials.email` was a write-once
copy of `Party.email`, made at `register()` and never touched again. Once
`parties/service.py::update_person_party` (Task 5) could edit or NULL
`Party.email`, the two columns could silently disagree — a person's visible
profile email and their actual login identity would drift apart with no
guard (feature independence means `parties` cannot reach into `auth`'s
table to keep them in sync).

This migration makes `Party.email` (core, `app/core/models.py`) the SINGLE
email authority: `user_credentials.email` and its
`uq_user_credentials_tenant_email` unique constraint are dropped.
`app/features/auth/service.py::login` now resolves the party by
`(tenant_id, lower(email), party_type='person')` first, then the credential
row by `party_id` — see that function's docstring for the anti-enumeration
analysis (unchanged posture, not widened).

Destructive by design (template project, no production data to preserve) —
`downgrade()` is a best-effort restore, not a full undo: it re-adds `email`
NULLABLE and backfills it from the CURRENT `parties.email` for every
existing credential row via a join on `party_id`, then restores the
`uq_user_credentials_tenant_email` unique constraint (safe even with NULL
values present — Postgres treats each NULL as distinct from every other
NULL under a UNIQUE constraint, and the backfilled values are already
unique-per-tenant because they come straight from `parties`, which enforces
its own `(tenant_id, lower(email))` partial unique index). It cannot restore
the historical MEANING of the column (a write-once copy of whatever email
existed at registration time) — only today's `Party.email`. A person party
with a NULL email (a legitimate, intended state per Task 3) downgrades to a
NULL credential email too, same as before Task 3 ever ran register() for
that row. Restoring the constraint (rather than leaving it dropped) is what
makes `upgrade() -> downgrade() -> upgrade()` a clean round-trip: `upgrade()`
unconditionally drops the constraint, so a downgrade that left it dropped
would make a second `upgrade()` fail on a constraint that no longer exists.

Revision ID: 0005_single_email_authority
Revises: 0004_custom_fields
Create Date: 2026-07-18

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_single_email_authority"
down_revision = "0004_custom_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_user_credentials_tenant_email", "user_credentials", type_="unique"
    )
    op.drop_column("user_credentials", "email")


def downgrade() -> None:
    op.add_column("user_credentials", sa.Column("email", sa.String(254), nullable=True))
    # Backfill note: the historical value ("whatever email `register()` saw
    # at signup time") is gone — the best available substitute is today's
    # `Party.email` via the `party_id` FK. A party with no email (intended
    # under Task 3, e.g. after an admin NULLed it) backfills to NULL here
    # too.
    op.execute(
        """
        UPDATE user_credentials
        SET email = parties.email
        FROM parties
        WHERE user_credentials.party_id = parties.id
          AND user_credentials.tenant_id = parties.tenant_id;
        """
    )
    op.create_unique_constraint(
        "uq_user_credentials_tenant_email", "user_credentials", ["tenant_id", "email"]
    )
