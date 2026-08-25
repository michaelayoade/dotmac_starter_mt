"""Add complimentary/sponsored arrangements and non-cash grants, additively.

``su_0001_subscriptions`` and ``su_0002_offer_pricing`` both shipped in
``dotmac-subscriptions 0.1.0a2`` and are immutable history: every byte of them
has already run in a database this repository does not own.  This revision
therefore only CREATES, and touches the released tables solely by attaching one
new trigger to the contract-version table.

The invariants below are structural on purpose.  A service check protects the
one writer that goes through it; a constraint protects the table.  Since the
whole point of the ported lifecycle is that nobody can quietly give service
away, the database is where the rules belong:

* ``ends_at`` is NOT NULL — a permanent exemption cannot be expressed at all;
* ``maximum_recurring_amount > 0`` — an approval always names a positive
  ceiling, so "approved for nothing" is not a state;
* the grant CHECK relates three amounts at once, refusing a non-positive
  contracted amount (zero-price concealment), a non-positive grant, and any
  grant above either the contracted amount or the approved cap;
* grants are append-only in PostgreSQL, so foregone-revenue evidence cannot be
  edited after the fact;
* an arrangement may only ever transition active -> revoked, with its
  commercial terms frozen, so "immutable price fields while open" is a trigger
  rather than a convention; and
* a NEW contract version for a contract with an OPEN arrangement is refused —
  the product-neutral equivalent of Sub's
  ``protect_subscription_billing_treatment_terms``.  Changing commercial terms
  requires revoking and reapproving, which is the review this exists to force.

Ported from ``dotmac_sub`` ``alembic/versions/399_subscription_billing_treatments.py``.
Deliberately NOT ported: Sub's ``public``-schema table names, its product
foreign keys (subscriptions, subscribers, catalog_offers), its
``service_entitlements`` column and partial index, its permission seeding, and
its three PostgreSQL ENUM types — ADR-0008 keeps the reason vocabulary an open
declared registry with a plain string column, so an eighth reason never costs a
module migration.

Revision ID: su_0003_billing_treatments
Revises: su_0002_offer_pricing
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0003_billing_treatments"
down_revision = "su_0002_offer_pricing"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _tenant() -> sa.Column[Any]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE", name=name
    )


def _created() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _arrangement_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("authorized_contract_version_id", sa.Uuid(), nullable=False),
        sa.Column("authorized_offer_version_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("treatment", sa.String(24), nullable=False),
        # ADR-0008: an OPEN declared vocabulary, so no CHECK re-closes it here.
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_policy_ref", sa.String(255), nullable=False),
        sa.Column("approval_policy_version", sa.String(80), nullable=False),
        sa.Column("approval_policy_max_days", sa.Integer(), nullable=False),
        sa.Column("maximum_recurring_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("service_interval_unit", sa.String(12), nullable=False),
        sa.Column("service_interval_count", sa.Integer(), nullable=False),
        sa.Column("sponsor_reference", sa.String(255), nullable=True),
        sa.Column("cost_center", sa.String(120), nullable=True),
        sa.Column("approved_by", sa.String(160), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("revoked_by", sa.String(160), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("revocation_command_id", sa.Uuid(), nullable=True),
        sa.Column("revocation_correlation_id", sa.Uuid(), nullable=True),
        sa.Column("revocation_idempotency_key", sa.String(255), nullable=True),
        _created(),
    ]


def _grant_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("arrangement_id", sa.Uuid(), nullable=False),
        sa.Column("recurring_occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("contract_line_key", sa.Uuid(), nullable=False),
        sa.Column("treatment", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contracted_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("approved_maximum_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("foregone_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
    ]


def _arrangement_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name=f"ck_{prefix}_nonstandard",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name=f"ck_{prefix}_status"
        ),
        sa.CheckConstraint("ends_at > starts_at", name=f"ck_{prefix}_period"),
        sa.CheckConstraint(
            "maximum_recurring_amount > 0", name=f"ck_{prefix}_positive_value"
        ),
        sa.CheckConstraint(
            "approval_policy_max_days > 0", name=f"ck_{prefix}_approval_policy"
        ),
        sa.CheckConstraint("scale >= 0 AND scale <= 6", name=f"ck_{prefix}_scale"),
        sa.CheckConstraint(
            "service_interval_count > 0", name=f"ck_{prefix}_interval_count"
        ),
        sa.CheckConstraint(
            "treatment <> 'sponsored' OR sponsor_reference IS NOT NULL "
            "OR cost_center IS NOT NULL",
            name=f"ck_{prefix}_sponsor_evidence",
        ),
        sa.CheckConstraint(
            "(status = 'active') = (revoked_at IS NULL)",
            name=f"ck_{prefix}_revocation_evidence",
        ),
    ]


def _grant_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "treatment IN ('complimentary', 'sponsored')",
            name=f"ck_{prefix}_nonstandard",
        ),
        sa.CheckConstraint("ends_at > starts_at", name=f"ck_{prefix}_period"),
        # The whole of G3 in one constraint: a positive contracted amount (no
        # zero-price concealment), a positive non-cash grant, and a grant that
        # exceeds neither what was contracted nor what was approved.
        sa.CheckConstraint(
            "contracted_amount > 0 AND approved_maximum_amount > 0 "
            "AND foregone_amount > 0 "
            "AND foregone_amount <= contracted_amount "
            "AND foregone_amount <= approved_maximum_amount",
            name=f"ck_{prefix}_bounded_non_cash_value",
        ),
        sa.CheckConstraint("scale >= 0 AND scale <= 6", name=f"ck_{prefix}_scale"),
    ]


def _upgrade_tenant() -> None:
    op.create_table(
        "subscription_billing_arrangements",
        _id(),
        _tenant(),
        *_arrangement_columns(),
        _tenant_fk("fk_billing_arrangements_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"],
            [
                f"{_SCHEMA}.subscription_contracts.tenant_id",
                f"{_SCHEMA}.subscription_contracts.id",
            ],
            name="fk_billing_arrangements_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "authorized_contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.subscription_contract_lines.tenant_id",
                f"{_SCHEMA}.subscription_contract_lines.contract_version_id",
                f"{_SCHEMA}.subscription_contract_lines.contract_line_key",
            ],
            name="fk_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "authorized_offer_version_id"],
            [
                f"{_SCHEMA}.offer_versions.tenant_id",
                f"{_SCHEMA}.offer_versions.id",
            ],
            name="fk_billing_arrangements_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "contract_id",
            "contract_line_key",
            "starts_at",
            name="uq_billing_arrangements_start",
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_billing_arrangements_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_billing_arrangements_command"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revocation_idempotency_key",
            name="uq_billing_arrangements_revocation_idempotency",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revocation_command_id",
            name="uq_billing_arrangements_revocation_command",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_billing_arrangements_tenant_id_id"
        ),
        *_arrangement_checks("billing_arrangements"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_billing_arrangements_effective",
        "subscription_billing_arrangements",
        [
            "tenant_id",
            "contract_id",
            "contract_line_key",
            "status",
            "starts_at",
            "ends_at",
        ],
        schema=_SCHEMA,
    )
    op.create_table(
        "subscription_billing_grants",
        _id(),
        _tenant(),
        *_grant_columns(),
        _tenant_fk("fk_billing_grants_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "arrangement_id"],
            [
                f"{_SCHEMA}.subscription_billing_arrangements.tenant_id",
                f"{_SCHEMA}.subscription_billing_arrangements.id",
            ],
            name="fk_billing_grants_arrangement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recurring_occurrence_id"],
            [
                f"{_SCHEMA}.recurring_charge_occurrences.tenant_id",
                f"{_SCHEMA}.recurring_charge_occurrences.id",
            ],
            name="fk_billing_grants_occurrence",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_billing_grants_period",
        ),
        sa.UniqueConstraint(
            "tenant_id", "recurring_occurrence_id", name="uq_billing_grants_occurrence"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_billing_grants_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_billing_grants_command"
        ),
        *_grant_checks("billing_grants"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_billing_grants_line_period",
        "subscription_billing_grants",
        ["tenant_id", "contract_line_key", "starts_at", "ends_at"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        ALTER TABLE mod_subscriptions.subscription_billing_arrangements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.subscription_billing_arrangements FORCE ROW LEVEL SECURITY;
        CREATE POLICY subscription_billing_arrangements_tenant_isolation ON mod_subscriptions.subscription_billing_arrangements
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        ALTER TABLE mod_subscriptions.subscription_billing_grants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_subscriptions.subscription_billing_grants FORCE ROW LEVEL SECURITY;
        CREATE POLICY subscription_billing_grants_tenant_isolation ON mod_subscriptions.subscription_billing_grants
          USING (tenant_id = public.app_current_tenant_id())
          WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.subscription_billing_arrangements TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.subscription_billing_grants TO app_user;
        """
    )


def _upgrade_platform() -> None:
    op.create_table(
        "platform_subscription_billing_arrangements",
        _id(),
        *_arrangement_columns(),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            [f"{_SCHEMA}.platform_subscription_contracts.id"],
            name="fk_platform_billing_arrangements_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorized_contract_version_id", "contract_line_key"],
            [
                f"{_SCHEMA}.platform_subscription_contract_lines.contract_version_id",
                f"{_SCHEMA}.platform_subscription_contract_lines.contract_line_key",
            ],
            name="fk_platform_billing_arrangements_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorized_offer_version_id"],
            [f"{_SCHEMA}.platform_offer_versions.id"],
            name="fk_platform_billing_arrangements_offer_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "contract_id",
            "contract_line_key",
            "starts_at",
            name="uq_platform_billing_arrangements_start",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_arrangements_idempotency"
        ),
        sa.UniqueConstraint(
            "command_id", name="uq_platform_billing_arrangements_command"
        ),
        sa.UniqueConstraint(
            "revocation_idempotency_key",
            name="uq_platform_billing_arrangements_revocation_idempotency",
        ),
        sa.UniqueConstraint(
            "revocation_command_id",
            name="uq_platform_billing_arrangements_revocation_command",
        ),
        *_arrangement_checks("platform_billing_arrangements"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_billing_arrangements_effective",
        "platform_subscription_billing_arrangements",
        ["contract_id", "contract_line_key", "status", "starts_at", "ends_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_subscription_billing_grants",
        _id(),
        *_grant_columns(),
        sa.ForeignKeyConstraint(
            ["arrangement_id"],
            [f"{_SCHEMA}.platform_subscription_billing_arrangements.id"],
            name="fk_platform_billing_grants_arrangement",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recurring_occurrence_id"],
            [f"{_SCHEMA}.platform_recurring_charge_occurrences.id"],
            name="fk_platform_billing_grants_occurrence",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "arrangement_id",
            "starts_at",
            "ends_at",
            name="uq_platform_billing_grants_period",
        ),
        sa.UniqueConstraint(
            "recurring_occurrence_id", name="uq_platform_billing_grants_occurrence"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_platform_billing_grants_idempotency"
        ),
        sa.UniqueConstraint("command_id", name="uq_platform_billing_grants_command"),
        *_grant_checks("platform_billing_grants"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_billing_grants_line_period",
        "platform_subscription_billing_grants",
        ["contract_line_key", "starts_at", "ends_at"],
        schema=_SCHEMA,
    )
    # On the control plane the REVOKE *is* the isolation (ADR-0023): there is
    # no tenant column to police, so the tenant application role must not be
    # able to read one row of it.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_subscription_billing_arrangements TO platform_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_subscriptions.platform_subscription_billing_grants TO platform_api;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_subscription_billing_arrangements FROM app_user;
        REVOKE ALL PRIVILEGES ON mod_subscriptions.platform_subscription_billing_grants FROM app_user;
        """
    )


def _install_structural_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_subscriptions.freeze_billing_arrangement_terms()
        RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'billing arrangements cannot be deleted'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF (to_jsonb(NEW) - ARRAY[
                'status', 'revoked_by', 'revoked_at', 'revocation_reason',
                'revocation_command_id', 'revocation_correlation_id',
                'revocation_idempotency_key'
              ]) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY[
                'status', 'revoked_by', 'revoked_at', 'revocation_reason',
                'revocation_command_id', 'revocation_correlation_id',
                'revocation_idempotency_key'
              ]) THEN
            RAISE EXCEPTION 'approved billing arrangement terms are immutable; revoke and reapprove'
              USING ERRCODE = 'restrict_violation';
          END IF;
          IF OLD.status <> 'active' OR NEW.status <> 'revoked'
             OR OLD.revoked_at IS NOT NULL
             OR NEW.revoked_at IS NULL
             OR NULLIF(BTRIM(NEW.revoked_by), '') IS NULL
             OR NULLIF(BTRIM(NEW.revocation_reason), '') IS NULL
             OR NEW.revocation_command_id IS NULL
             OR NEW.revocation_correlation_id IS NULL
             OR NULLIF(BTRIM(NEW.revocation_idempotency_key), '') IS NULL THEN
            RAISE EXCEPTION 'billing arrangement update is not a valid revocation'
              USING ERRCODE = 'restrict_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.prevent_tenant_arrangement_overlap()
        RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            NEW.tenant_id::text || ':' || NEW.contract_id::text || ':'
            || NEW.contract_line_key::text, 0));
          IF NEW.status = 'active' AND EXISTS (
            SELECT 1 FROM mod_subscriptions.subscription_billing_arrangements a
             WHERE a.tenant_id = NEW.tenant_id
               AND a.contract_id = NEW.contract_id
               AND a.contract_line_key = NEW.contract_line_key
               AND a.id <> NEW.id
               AND a.status = 'active'
               AND a.starts_at < NEW.ends_at
               AND NEW.starts_at < a.ends_at
          ) THEN
            RAISE EXCEPTION 'billing arrangement periods overlap for one contract line'
              USING ERRCODE = 'exclusion_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.prevent_platform_arrangement_overlap()
        RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(
            NEW.contract_id::text || ':' || NEW.contract_line_key::text, 0));
          IF NEW.status = 'active' AND EXISTS (
            SELECT 1 FROM mod_subscriptions.platform_subscription_billing_arrangements a
             WHERE a.contract_id = NEW.contract_id
               AND a.contract_line_key = NEW.contract_line_key
               AND a.id <> NEW.id
               AND a.status = 'active'
               AND a.starts_at < NEW.ends_at
               AND NEW.starts_at < a.ends_at
          ) THEN
            RAISE EXCEPTION 'platform billing arrangement periods overlap for one contract line'
              USING ERRCODE = 'exclusion_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.protect_tenant_treatment_terms()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM mod_subscriptions.subscription_billing_arrangements a
             WHERE a.tenant_id = NEW.tenant_id
               AND a.contract_id = NEW.contract_id
               AND a.status = 'active'
               AND a.ends_at > CURRENT_TIMESTAMP
               AND a.authorized_contract_version_id <> NEW.id
          ) THEN
            RAISE EXCEPTION
              'revoke the open billing treatment before changing commercial terms'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION mod_subscriptions.protect_platform_treatment_terms()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM mod_subscriptions.platform_subscription_billing_arrangements a
             WHERE a.contract_id = NEW.contract_id
               AND a.status = 'active'
               AND a.ends_at > CURRENT_TIMESTAMP
               AND a.authorized_contract_version_id <> NEW.id
          ) THEN
            RAISE EXCEPTION
              'revoke the open billing treatment before changing commercial terms'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('mod_subscriptions.subscription_billing_arrangements') IS NOT NULL THEN
            CREATE TRIGGER billing_arrangements_term_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_billing_arrangement_terms();
            CREATE TRIGGER billing_arrangements_no_overlap
              BEFORE INSERT OR UPDATE ON mod_subscriptions.subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.prevent_tenant_arrangement_overlap();
            CREATE TRIGGER billing_grants_append_only
              BEFORE UPDATE OR DELETE ON mod_subscriptions.subscription_billing_grants
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER contract_versions_treatment_term_freeze
              BEFORE INSERT ON mod_subscriptions.subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.protect_tenant_treatment_terms();
          END IF;
          IF to_regclass('mod_subscriptions.platform_subscription_billing_arrangements') IS NOT NULL THEN
            CREATE TRIGGER platform_billing_arrangements_term_freeze
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.freeze_billing_arrangement_terms();
            CREATE TRIGGER platform_billing_arrangements_no_overlap
              BEFORE INSERT OR UPDATE ON mod_subscriptions.platform_subscription_billing_arrangements
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.prevent_platform_arrangement_overlap();
            CREATE TRIGGER platform_billing_grants_append_only
              BEFORE UPDATE OR DELETE ON mod_subscriptions.platform_subscription_billing_grants
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.refuse_immutable_row();
            CREATE TRIGGER platform_contract_versions_treatment_term_freeze
              BEFORE INSERT ON mod_subscriptions.platform_subscription_contract_versions
              FOR EACH ROW EXECUTE FUNCTION mod_subscriptions.protect_platform_treatment_terms();
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    if ModulePlane.TENANT in planes:
        _upgrade_tenant()
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform()
    _install_structural_guards()


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    op.execute(
        """
        DO $$ BEGIN
          IF to_regclass('mod_subscriptions.subscription_contract_versions') IS NOT NULL THEN
            DROP TRIGGER IF EXISTS contract_versions_treatment_term_freeze
              ON mod_subscriptions.subscription_contract_versions;
          END IF;
          IF to_regclass('mod_subscriptions.platform_subscription_contract_versions') IS NOT NULL THEN
            DROP TRIGGER IF EXISTS platform_contract_versions_treatment_term_freeze
              ON mod_subscriptions.platform_subscription_contract_versions;
          END IF;
        END $$;
        """
    )
    if ModulePlane.PLATFORM in planes:
        op.drop_table("platform_subscription_billing_grants", schema=_SCHEMA)
        op.drop_table("platform_subscription_billing_arrangements", schema=_SCHEMA)
    if ModulePlane.TENANT in planes:
        op.drop_table("subscription_billing_grants", schema=_SCHEMA)
        op.drop_table("subscription_billing_arrangements", schema=_SCHEMA)
    op.execute(
        """
        DROP FUNCTION IF EXISTS mod_subscriptions.protect_platform_treatment_terms();
        DROP FUNCTION IF EXISTS mod_subscriptions.protect_tenant_treatment_terms();
        DROP FUNCTION IF EXISTS mod_subscriptions.prevent_platform_arrangement_overlap();
        DROP FUNCTION IF EXISTS mod_subscriptions.prevent_tenant_arrangement_overlap();
        DROP FUNCTION IF EXISTS mod_subscriptions.freeze_billing_arrangement_terms();
        """
    )
