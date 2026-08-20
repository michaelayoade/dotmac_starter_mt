"""Commercial agreement tables — the `commercial_agreements` lineage root.

Lineage ROOT: `down_revision = None`, `branch_labels` names the owner, and the
only `depends_on` edge is the LOGICAL one the assembly binds (ADR-0006 D1
amendment). There is no edge to a contract, offer, release, approval or
counterparty table, and there is no foreign key to one either — every reference
that leaves this schema is a bare string or UUID with no constraint.

That absence is the design, not an omission. A cross-module FK splices two
independently released lineages and makes either un-releasable without the
other; it also makes an executed agreement deletable by deleting a row that
merely describes something it references. An agreement is a legal record that
must outlive a superseded release, a retired approval policy and a merged
counterparty record.

## Platform catalog grants, not RLS

A vendor↔operator agreement is a control-plane fact with no `tenant_id` to scope
by, so RLS would have no predicate. Grants instead (hard rule 27):

- `platform_api` — the ONLINE request-path role. SELECT and INSERT on all three
  tables, plus UPDATE on `agreements` only. The lifecycle lives on the header,
  so that is where UPDATE has to exist; `agreement_lines` and `agreement_events`
  get none, which is what makes them immutable to the online role.
- `app_admin` — the OFFLINE migration role. Full access on the header and the
  lines, so a mis-entered agreement is correctable under review. Deliberately
  NOT full access on `agreement_events`: see below.
- `app_user` — the tenant data-plane role. REVOKEd from all three. On this plane
  the revoke IS the isolation, and it is checked as strictly as a policy is on
  the other side.

## Append-only is a trigger, not a convention

`agreement_events` refuses UPDATE and DELETE from EVERY role, including
`app_admin`, through `refuse_history_rewrite`. Two reasons, and the second is
the one that matters:

1. A service rule cannot police a path that never calls the service. Withholding
   UPDATE from `platform_api` stops the application; it does not stop `psql`.
2. An evidence history an administrator can rewrite is not evidence. Every other
   table here is correctable by `app_admin` precisely because correcting a typo
   in a counterparty reference is legitimate. Rewriting the record of who
   approved what, and when, is not — it is the one operation whose availability
   destroys the value of the whole table. The correct repair for a wrong history
   row is a compensating transition, which appends.

`ondelete="RESTRICT"` on `agreement_events.agreement_id` closes the same hole
from the other side: without it, deleting an agreement would cascade its history
away, and "delete then re-create" would launder a rewrite through a path the
trigger never sees.

## The lines cascade; the history does not

`agreement_lines` uses `ondelete="CASCADE"` because a line has no meaning apart
from its agreement and no independent audit value — the accepted snapshot on the
header is the record of what was agreed. The history has independent value, so
it restricts.

## No CHECK on `capability_code`, `agreement_type` or `status`

The vocabulary belongs to the PRODUCTS, is manifest-derived, and differs per
product — a constraint here would need to be a different constraint per row.
ADR-0008's reason for status: adding a lifecycle member should cost a module
release, not an `ALTER TYPE` on every deployment. Legality is proven by the
service's guard and by the append-only history, both of which are testable.

The CHECKs that ARE here constrain things that are true independent of any
vocabulary: a version is at least 1, a quantity is positive, and an agreement
cannot end before it starts.

Revision ID: cg_0001_agreements
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "cg_0001_agreements"
down_revision = None
branch_labels = ("commercial_agreements",)

# COMMON, and the three lists are written out in full so the reader sees the
# decision rather than an omission. This module owns exactly one plane
# (`tables` is empty, `supported_plane_sets` unset), so the platform plane
# installs atomically and no selection exists under which either effect stops
# being needed. A plane-specific list would condition on something that cannot
# vary — and would be unresolvable, because `resolve_depends_on` reads a plane
# list only via `module=`, which reads `selected_module_planes`, which an
# atomic module may never have.
#
# Both effects are written at REQUEST time by every command:
# `process_once_platform` writes `public.platform_idempotency_records`, and
# `write_platform_audit_event` writes `public.platform_audit_events` inside the
# same operation. Neither is created here. Declared at the lineage root rather
# than in a later verification revision because `cg_0001` has never been
# published — the reason `dotmac-numbering` could take this route in kernel a66
# and `dotmac-entitlement-allocation` could not.
COMMON_REQUIRES = ("idempotency_ledger.v1", "platform_audit_log.v1")
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

# The assembly binds each effect to the revision that SUPPLIES it. This file
# names neither; that is the point of the indirection
# (`app/migration_bindings.py` in each assembly).
depends_on = resolve_depends_on(COMMON_REQUIRES)

# A literal, not `module_schema("agreements")`. A migration is a frozen
# historical artifact, and the static gate reads this file without importing it.
_SCHEMA = "mod_agreements"


def upgrade() -> None:
    # Prove the two request-time effects BEFORE any DDL of this module's own
    # runs. Deploy is the last moment at which a missing ledger is a failed
    # migration rather than a failed transition in production.
    require_prerequisites(op.get_bind(), REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_agreements;")
    op.execute("GRANT USAGE ON SCHEMA mod_agreements TO platform_api, app_admin;")

    op.create_table(
        "agreements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("agreement_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agreement_version", sa.Integer(), nullable=False),
        sa.Column("counterparty_ref", sa.String(length=200), nullable=False),
        sa.Column("agreement_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("accepted_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("approval_policy_code", sa.String(length=120), nullable=True),
        sa.Column("approval_policy_version", sa.Integer(), nullable=True),
        sa.Column("approval_decision_ref", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activation_rule", sa.String(length=120), nullable=True),
        sa.Column("activation_reference", sa.String(length=200), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspension_reason", sa.Text(), nullable=True),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("last_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("record_version", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint("reference", name="uq_agreements_reference"),
        sa.UniqueConstraint(
            "agreement_family_id",
            "agreement_version",
            name="uq_agreements_family_version",
        ),
        sa.CheckConstraint("agreement_version >= 1", name="ck_agreements_version"),
        sa.CheckConstraint("record_version >= 1", name="ck_agreements_record_version"),
        sa.CheckConstraint(
            "expiry_date >= effective_date", name="ck_agreements_period"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["mod_agreements.agreements.id"],
            name="fk_agreements_supersedes_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["mod_agreements.agreements.id"],
            name="fk_agreements_superseded_by_id",
            ondelete="RESTRICT",
        ),
        schema="mod_agreements",
    )
    op.create_index(
        "ix_agreements_counterparty_ref",
        "agreements",
        ["counterparty_ref"],
        schema="mod_agreements",
    )
    op.create_index(
        "ix_agreements_family_id",
        "agreements",
        ["agreement_family_id"],
        schema="mod_agreements",
    )
    op.create_index(
        "ix_agreements_status", "agreements", ["status"], schema="mod_agreements"
    )

    op.create_table(
        "agreement_lines",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("release_ref", sa.String(length=200), nullable=True),
        sa.Column("offer_ref", sa.String(length=200), nullable=True),
        sa.Column("capability_code", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.String(length=40), nullable=False),
        sa.Column("unit_currency_code", sa.String(length=3), nullable=False),
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
            ["agreement_id"],
            ["mod_agreements.agreements.id"],
            name="fk_agreement_lines_agreement_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("agreement_id", "line_no", name="uq_agreement_lines_no"),
        # A service rule cannot police a path that never calls the service.
        sa.CheckConstraint("quantity > 0", name="ck_agreement_lines_quantity"),
        schema="mod_agreements",
    )
    op.create_index(
        "ix_agreement_lines_agreement_id",
        "agreement_lines",
        ["agreement_id"],
        schema="mod_agreements",
    )

    op.create_table(
        "agreement_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("actor_ref", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("command_id", sa.String(length=200), nullable=False),
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
        # RESTRICT, not CASCADE. Without it, "delete the agreement" would launder
        # a history rewrite through a path `refuse_history_rewrite` never sees.
        sa.ForeignKeyConstraint(
            ["agreement_id"],
            ["mod_agreements.agreements.id"],
            name="fk_agreement_events_agreement_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "agreement_id", "sequence", name="uq_agreement_events_sequence"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_agreement_events_sequence"),
        schema="mod_agreements",
    )
    op.create_index(
        "ix_agreement_events_agreement_id",
        "agreement_events",
        ["agreement_id"],
        schema="mod_agreements",
    )

    # ── Append-only, enforced against every role ────────────────────────────
    #
    # Withholding UPDATE and DELETE from `platform_api` stops the application.
    # It does not stop `psql`, and `app_admin` legitimately holds full DML on
    # the other two tables. A trigger is the only place the rule holds for both.
    op.execute(
        """
        CREATE FUNCTION mod_agreements.refuse_history_rewrite() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'agreement_events is append-only; correct a transition by '
                'appending a compensating one, never by rewriting history'
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER refuse_history_rewrite
        BEFORE UPDATE OR DELETE ON mod_agreements.agreement_events
        FOR EACH ROW EXECUTE FUNCTION mod_agreements.refuse_history_rewrite();
        """
    )

    # Written out per table and per role rather than looped: these lines are the
    # module's entire access-control surface and should be greppable.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_agreements.agreements TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_agreements.agreement_lines TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_agreements.agreement_events TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_agreements.agreements "
        "TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_agreements.agreement_lines "
        "TO app_admin;"
    )
    # SELECT and INSERT only, even for the offline role. See the module
    # docstring: the correct repair for a wrong history row is a compensating
    # transition, which appends.
    op.execute("GRANT SELECT, INSERT ON mod_agreements.agreement_events TO app_admin;")
    op.execute("REVOKE ALL ON mod_agreements.agreements FROM app_user;")
    op.execute("REVOKE ALL ON mod_agreements.agreement_lines FROM app_user;")
    op.execute("REVOKE ALL ON mod_agreements.agreement_events FROM app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS refuse_history_rewrite "
        "ON mod_agreements.agreement_events;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_agreements.refuse_history_rewrite();")
    op.drop_index(
        "ix_agreement_events_agreement_id",
        "agreement_events",
        schema="mod_agreements",
    )
    op.drop_table("agreement_events", schema="mod_agreements")
    op.drop_index(
        "ix_agreement_lines_agreement_id",
        "agreement_lines",
        schema="mod_agreements",
    )
    op.drop_table("agreement_lines", schema="mod_agreements")
    op.drop_index("ix_agreements_status", "agreements", schema="mod_agreements")
    op.drop_index("ix_agreements_family_id", "agreements", schema="mod_agreements")
    op.drop_index(
        "ix_agreements_counterparty_ref", "agreements", schema="mod_agreements"
    )
    op.drop_table("agreements", schema="mod_agreements")
    op.execute("DROP SCHEMA IF EXISTS mod_agreements RESTRICT;")
