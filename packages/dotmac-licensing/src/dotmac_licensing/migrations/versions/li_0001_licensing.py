"""Licence tables — the `licensing` lineage root.

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and the
only `depends_on` edge is the LOGICAL one the assembly binds (ADR-0006 D1
amendment). No edge to an agreement, allocation, release or deployment table,
and no foreign key to one either — `subject_ref`, `agreement_ref`,
`allocation_ref` and `deployment_ref` are bare strings with no constraint.

A licence is enforceable authority that must stay verifiable after the agreement
row is archived and the allocation's retention has passed. A cross-lineage FK
would also splice three module lineages into one release unit, which D1 forbids.

## There is no private-key column

`signing_keys` holds the public half only. Custody is the product's, behind the
`LicenceSigner` port (ADR-0009, hard rule 20). A database dump, a backup, a
replica and a `SELECT *` in a support session are all structurally incapable of
leaking signing material, because the column does not exist.

## Platform catalog grants, not RLS

Issuance is a control-plane act; there is no `tenant_id` to scope by, so RLS
would have no predicate. Grants instead (hard rule 27):

- `platform_api` — the ONLINE role. SELECT and INSERT on all six tables, plus
  UPDATE on `signing_keys` (rotation) and `licence_issuances` (the lifecycle).
  `licences`, `licence_acknowledgements`, `revocations` and `revocation_lists`
  get no UPDATE, which is what makes them immutable to the online role.
- `app_admin` — the OFFLINE migration role. Full DML on `signing_keys`,
  `licences` and `licence_issuances`. Deliberately SELECT and INSERT only on the
  three evidence tables — see below.
- `app_user` — the tenant data-plane role. REVOKEd from all six. On this plane
  the revoke IS the isolation, and it is checked as strictly as a policy is on
  the other side. A data plane learns what it may do from a signed document it
  verifies offline, never by reading the issuer's tables.

## Three append-only tables, enforced by trigger against every role

`licence_acknowledgements`, `revocations` and `revocation_lists` refuse UPDATE
and DELETE from EVERY role, including `app_admin`, through
`refuse_evidence_rewrite`.

A service rule cannot police a path that never calls the service, and each of
these three is evidence whose value is precisely that it cannot be adjusted:

- an acknowledgement is a remote party's claim about what it installed. If the
  issuer can rewrite it, it is the issuer's opinion, not the party's report.
- a revocation is permanent by contract. A deletable revocation row is an
  un-revoke by another name — the exact operation the cumulative rule exists to
  make impossible.
- a published revocation list is an artifact the fleet already holds. Editing
  the issuer's copy would make the issuer's record disagree with every
  deployment's, silently and in the dangerous direction.

`ondelete="RESTRICT"` on the licence and issuance foreign keys closes the same
holes from the other side: without it, "delete the parent" would launder a
rewrite through a path the trigger never sees.

## Two uniqueness rules that are load-bearing

- `licence_issuances.allocation_ref` is unique. One issued version per staged
  allocation; two would mean the same entitlement authorised twice, which is
  exactly what an idempotent issuer must make impossible — and the service check
  cannot police a path that never calls it.
- `licence_issuances.digest` is unique. Two rows claiming one payload digest
  means either a duplicate issuance or a digest computed over the wrong bytes;
  both are defects that must fail at write time rather than at a receiver.

## No CHECK on `status` or `product_code`

ADR-0008's reason: adding a lifecycle member should cost a module release, not
an `ALTER TYPE` on every deployment. The vocabulary of products belongs to the
products. The CHECKs that ARE here constrain things true independent of any
vocabulary: versions and generations start at 1, grace days are non-negative,
and a list cannot have a negative entry count.

Revision ID: li_0001_licensing
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "li_0001_licensing"
down_revision = None
branch_labels = ("licensing",)

# COMMON, and the three lists are written out in full so the reader sees the
# decision rather than an omission. This module owns exactly one plane
# (`tables` is empty, `supported_plane_sets` unset), so the platform plane
# installs atomically and no selection exists under which either effect stops
# being needed.
#
# Both effects are written at REQUEST time by every command:
# `process_once_platform` writes `public.platform_idempotency_records`, and
# `write_platform_audit_event` writes `public.platform_audit_events` inside the
# same operation. Neither is created here.
COMMON_REQUIRES = ("idempotency_ledger.v1", "platform_audit_log.v1")
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

# The assembly binds each effect to the revision that SUPPLIES it. This file
# names neither; that is the point of the indirection.
depends_on = resolve_depends_on(COMMON_REQUIRES)

# A literal, not `module_schema("licensing")`. A migration is a frozen
# historical artifact, and the static gate reads this file without importing it.
_SCHEMA = "mod_licensing"

#: The three tables whose whole value is that nobody can adjust them.
_EVIDENCE_TABLES = ("licence_acknowledgements", "revocations", "revocation_lists")


def _grant(privileges: str, table: str, role: str) -> None:
    """One GRANT, spelled out at the call site.

    A helper rather than an inline `op.execute` only because the statement text
    is otherwise identical eight times over; the CALL SITES stay one line each
    and fully literal, so the module's whole access-control surface remains
    greppable and statically checkable — which a loop over a table tuple would
    not be, and which the architecture guard depends on.
    """
    op.execute(f"GRANT {privileges} ON mod_licensing.{table} TO {role};")


def _revoke(table: str) -> None:
    """Revoke everything from the tenant app role. On this plane the revoke IS
    the isolation (hard rule 27)."""
    op.execute(f"REVOKE ALL ON mod_licensing.{table} FROM app_user;")


def upgrade() -> None:
    # Prove the two request-time effects BEFORE any DDL of this module's own
    # runs. Deploy is the last moment at which a missing ledger is a failed
    # migration rather than a failed issuance in production.
    require_prerequisites(op.get_bind(), REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_licensing;")
    op.execute("GRANT USAGE ON SCHEMA mod_licensing TO platform_api, app_admin;")

    op.create_table(
        "signing_keys",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("key_id", sa.String(length=120), nullable=False),
        # PUBLIC material only. There is deliberately no sibling column for the
        # private half — see the module docstring.
        sa.Column("public_key_b64", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.UniqueConstraint("key_id", name="uq_signing_keys_key_id"),
        schema="mod_licensing",
    )

    op.create_table(
        "licences",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("subject_ref", sa.String(length=200), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
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
            "subject_ref",
            "product_code",
            "generation",
            name="uq_licences_subject_product_generation",
        ),
        sa.CheckConstraint("generation >= 1", name="ck_licences_generation"),
        schema="mod_licensing",
    )
    op.create_index(
        "ix_licences_subject_ref", "licences", ["subject_ref"], schema="mod_licensing"
    )

    op.create_table(
        "licence_issuances",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("licence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("agreement_ref", sa.String(length=200), nullable=False),
        sa.Column("allocation_ref", sa.String(length=200), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("key_id", sa.String(length=120), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_days", sa.Integer(), nullable=False),
        sa.Column("deployment_ref", sa.String(length=200), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("replaced_by_version", sa.Integer(), nullable=True),
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
            ["licence_id"],
            ["mod_licensing.licences.id"],
            name="fk_licence_issuances_licence_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("licence_id", "version", name="uq_issuance_version"),
        # One issued version per staged allocation. The service checks this too;
        # the constraint is what polices a path that never calls the service.
        sa.UniqueConstraint("allocation_ref", name="uq_issuance_allocation"),
        # Two rows claiming one payload digest is either a duplicate issuance or
        # a digest computed over the wrong bytes. Both must fail here rather
        # than at a receiver.
        sa.UniqueConstraint("digest", name="uq_issuance_digest"),
        sa.CheckConstraint("version >= 1", name="ck_issuance_version"),
        sa.CheckConstraint("record_version >= 1", name="ck_issuance_record_version"),
        sa.CheckConstraint("grace_days >= 0", name="ck_issuance_grace_days"),
        schema="mod_licensing",
    )
    op.create_index(
        "ix_licence_issuances_licence_id",
        "licence_issuances",
        ["licence_id"],
        schema="mod_licensing",
    )
    op.create_index(
        "ix_licence_issuances_status",
        "licence_issuances",
        ["status"],
        schema="mod_licensing",
    )

    op.create_table(
        "licence_acknowledgements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("issuance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("licence_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_deployment_ref", sa.String(length=200), nullable=False),
        # What the TRANSPORT authenticated, separate from what the report claims
        # about itself. One column holding both would make "did we verify this?"
        # unanswerable after the fact.
        sa.Column("authenticated_deployment_ref", sa.String(length=200), nullable=True),
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
            ["issuance_id"],
            ["mod_licensing.licence_issuances.id"],
            name="fk_licence_acknowledgements_issuance_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "issuance_id",
            "outcome",
            "reported_deployment_ref",
            name="uq_ack_issuance_outcome_deployment",
        ),
        schema="mod_licensing",
    )
    op.create_index(
        "ix_licence_acknowledgements_issuance_id",
        "licence_acknowledgements",
        ["issuance_id"],
        schema="mod_licensing",
    )

    op.create_table(
        "revocations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("licence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("actor_ref", sa.String(length=200), nullable=True),
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
            ["licence_id"],
            ["mod_licensing.licences.id"],
            name="fk_revocations_licence_id",
            ondelete="RESTRICT",
        ),
        # Revoking twice is idempotent rather than a duplicated fact.
        sa.UniqueConstraint("licence_id", name="uq_revocations_licence"),
        schema="mod_licensing",
    )

    op.create_table(
        "revocation_lists",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("list_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("key_id", sa.String(length=120), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
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
        sa.UniqueConstraint("list_version", name="uq_revocation_list_version"),
        sa.CheckConstraint("list_version >= 1", name="ck_revocation_list_version"),
        sa.CheckConstraint("entry_count >= 0", name="ck_revocation_list_entry_count"),
        schema="mod_licensing",
    )

    # ── Append-only evidence, enforced against every role ───────────────────
    op.execute(
        """
        CREATE FUNCTION mod_licensing.refuse_evidence_rewrite() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                '%.% is append-only evidence; correct it by appending, never '
                'by rewriting', TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # Written out per table rather than looped, for the same reason the grants
    # below are: a loop is invisible to a static reader, to `grep`, and to the
    # architecture guard that checks this migration covers every evidence table.
    op.execute(
        """
        CREATE TRIGGER refuse_evidence_rewrite
        BEFORE UPDATE OR DELETE ON mod_licensing.licence_acknowledgements
        FOR EACH ROW EXECUTE FUNCTION mod_licensing.refuse_evidence_rewrite();
        """
    )
    op.execute(
        """
        CREATE TRIGGER refuse_evidence_rewrite
        BEFORE UPDATE OR DELETE ON mod_licensing.revocations
        FOR EACH ROW EXECUTE FUNCTION mod_licensing.refuse_evidence_rewrite();
        """
    )
    op.execute(
        """
        CREATE TRIGGER refuse_evidence_rewrite
        BEFORE UPDATE OR DELETE ON mod_licensing.revocation_lists
        FOR EACH ROW EXECUTE FUNCTION mod_licensing.refuse_evidence_rewrite();
        """
    )

    # Written out per table and per role rather than looped: these lines are the
    # module's entire access-control surface and should be greppable.
    _grant("SELECT, INSERT", "signing_keys", "platform_api")
    _grant("UPDATE", "signing_keys", "platform_api")
    _grant("SELECT, INSERT", "licences", "platform_api")
    _grant("SELECT, INSERT", "licence_issuances", "platform_api")
    _grant("UPDATE", "licence_issuances", "platform_api")
    _grant("SELECT, INSERT", "licence_acknowledgements", "platform_api")
    _grant("SELECT, INSERT", "revocations", "platform_api")
    _grant("SELECT, INSERT", "revocation_lists", "platform_api")

    _grant("SELECT, INSERT, UPDATE, DELETE", "signing_keys", "app_admin")
    _grant("SELECT, INSERT, UPDATE, DELETE", "licences", "app_admin")
    _grant("SELECT, INSERT, UPDATE, DELETE", "licence_issuances", "app_admin")
    # SELECT and INSERT only, even for the offline role — see the module
    # docstring for why each of these three must be unadjustable.
    _grant("SELECT, INSERT", "licence_acknowledgements", "app_admin")
    _grant("SELECT, INSERT", "revocations", "app_admin")
    _grant("SELECT, INSERT", "revocation_lists", "app_admin")

    _revoke("signing_keys")
    _revoke("licences")
    _revoke("licence_issuances")
    _revoke("licence_acknowledgements")
    _revoke("revocations")
    _revoke("revocation_lists")


def downgrade() -> None:
    for table in _EVIDENCE_TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS refuse_evidence_rewrite "
            f"ON mod_licensing.{table};"
        )
    op.execute("DROP FUNCTION IF EXISTS mod_licensing.refuse_evidence_rewrite();")
    op.drop_table("revocation_lists", schema="mod_licensing")
    op.drop_table("revocations", schema="mod_licensing")
    op.drop_index(
        "ix_licence_acknowledgements_issuance_id",
        "licence_acknowledgements",
        schema="mod_licensing",
    )
    op.drop_table("licence_acknowledgements", schema="mod_licensing")
    op.drop_index(
        "ix_licence_issuances_status", "licence_issuances", schema="mod_licensing"
    )
    op.drop_index(
        "ix_licence_issuances_licence_id", "licence_issuances", schema="mod_licensing"
    )
    op.drop_table("licence_issuances", schema="mod_licensing")
    op.drop_index("ix_licences_subject_ref", "licences", schema="mod_licensing")
    op.drop_table("licences", schema="mod_licensing")
    op.drop_table("signing_keys", schema="mod_licensing")
    op.execute("DROP SCHEMA IF EXISTS mod_licensing RESTRICT;")
