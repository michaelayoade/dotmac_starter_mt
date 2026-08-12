"""The release catalogue's schema and tables — the third module lineage (ADR-0006 D1).

Lineage ROOT: `down_revision = None` and `branch_labels` names the owner. Unlike
`tk_0001`, there is **no `depends_on`**: neither table references `tenants` or
any other kernel table, because both are platform catalog tables. Declaring a
dependency that does not exist would order this lineage behind a table it never
touches, for no reason, and make the module harder to install standalone.

Everything is fully qualified to `mod_rel`.

## Platform catalog grants, not RLS

Hard rule 11 requires `tenant_id NOT NULL` + composite uniques + RLS for
tenant-scoped tables, and names platform catalog tables as taking grants
instead. These are the latter: a published artifact is one fact for the whole
vendor, so there is no tenant column to scope by and RLS would have no predicate
to enforce.

GRANT to `platform_api` and `app_admin`; **REVOKE from `app_user`**. The revoke
is the load-bearing half. `app_user` is the product data plane's role, and a
data plane must learn which artifact to run from a signed licence or a
deployment plan — never by reading the vendor's catalogue directly. Without the
revoke, "the fleet parts are vendor-assembly-only" would be an import-linter
contract that a raw SQL query walks straight around.

## No CHECK constraint on `artifact_kind` or `attestation_kind`

Both vocabularies are closed in `dotmac_release_catalog.vocabulary` and stored
as plain text, the same split `tk_0001` documents for ticket status. A CHECK is
an `ALTER TABLE` on every deployment the day a fourth kind is justified; a
closed Python union is a module version. Enforcement happens on the way in,
where it is also testable.

## No CHECK constraint on `digest` either

It is tempting — the format is exactly `^[a-z0-9]+:[0-9a-f]+$`. It is omitted
for the same migration-cost reason, and because a regex constraint would prove
the *shape* while `identity.Digest` proves the shape AND the algorithm
allowlist AND the exact hex width. A weaker check beside a stronger one invites
the strong one to be skipped.

Revision ID: rl_0001_release_artifacts
Revises: (lineage root)
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "rl_0001_release_artifacts"
down_revision = None
branch_labels = ("release_catalog",)
depends_on = None

# A literal, not `module_schema("rel")`. A migration is a frozen historical
# artifact and must keep building the same schema even if a future kernel
# changes how a name is derived; the static gate also reads this file without
# importing it, so a computed name would be uninspectable.
_SCHEMA = "mod_rel"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_rel;")
    op.execute("GRANT USAGE ON SCHEMA mod_rel TO platform_api, app_admin;")

    op.create_table(
        "release_artifacts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=120), nullable=False),
        sa.Column("artifact_kind", sa.String(length=40), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("artifact_ref", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source_revision", sa.String(length=120), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("digest", name="uq_release_artifacts_digest"),
        sa.UniqueConstraint(
            "product_code",
            "version",
            "artifact_kind",
            name="uq_release_artifacts_product_version_kind",
        ),
        schema="mod_rel",
    )

    op.create_table(
        "artifact_attestations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attestation_kind", sa.String(length=40), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
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
            ["artifact_id"],
            ["mod_rel.release_artifacts.id"],
            name="fk_artifact_attestations_artifact_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "attestation_kind",
            "digest",
            name="uq_artifact_attestations_artifact_kind_digest",
        ),
        schema="mod_rel",
    )
    op.create_index(
        "ix_artifact_attestations_artifact_id",
        "artifact_attestations",
        ["artifact_id"],
        schema="mod_rel",
    )

    # Written out per table and per role rather than looped. The docstring above
    # already commits this file to being readable WITHOUT importing it — that is
    # why the schema is a literal — and a grant assembled from an f-string in a
    # nested loop is exactly as uninspectable as a computed schema name. These
    # eight lines are the module's entire access-control surface; they should be
    # greppable.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_rel.release_artifacts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_rel.release_artifacts TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_rel.artifact_attestations TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_rel.artifact_attestations TO app_admin;"
    )
    # The load-bearing pair: the product data plane's role cannot read the
    # vendor's catalogue at all.
    op.execute("REVOKE ALL ON mod_rel.release_artifacts FROM app_user;")
    op.execute("REVOKE ALL ON mod_rel.artifact_attestations FROM app_user;")


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_attestations_artifact_id",
        "artifact_attestations",
        schema="mod_rel",
    )
    op.drop_table("artifact_attestations", schema="mod_rel")
    op.drop_table("release_artifacts", schema="mod_rel")
    op.execute("DROP SCHEMA IF EXISTS mod_rel RESTRICT;")
