"""Immutable human-reviewed merchant configuration contexts; no cash admission.

Revision ID: pm_0005_merchant_context
Revises: pm_0004_purchase_funding
"""

from __future__ import annotations

# Closed Payments-owned schema/table identifiers only.
# ruff: noqa: S608
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pm_0005_merchant_context"
down_revision = "pm_0004_purchase_funding"
branch_labels = None
REQUIRES = ("outbox_relay.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_payments"
_TABLES = ("merchant_contexts", "merchant_context_ends")


def _review_columns() -> (
    tuple[sa.Column[UUID] | sa.Column[str] | sa.Column[datetime], ...]
):
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_reference", sa.String(512), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("reviewer_reference", sa.String(160), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
    )


def _review_constraints(table: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="RESTRICT",
            name=f"fk_{table}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id"),
        sa.UniqueConstraint("tenant_id", "command_id", name=f"uq_{table}_command"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name=f"ck_{table}_review_digests",
        ),
        sa.CheckConstraint(
            "evidence_reference ~ '[^[:space:]]' AND reviewer_reference ~ '[^[:space:]]'",
            name=f"ck_{table}_review_text",
        ),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "merchant_contexts",
        *_review_columns(),
        sa.Column("provider_type", sa.String(40), nullable=False),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("physical_merchant_id", sa.String(160), nullable=False),
        sa.Column("installation_reference", sa.String(160), nullable=False),
        sa.Column("capability_reference", sa.String(160), nullable=False),
        sa.Column("config_revision_reference", sa.String(160), nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        *_review_constraints("merchant_contexts"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_merchant_contexts_interval",
        ),
        sa.CheckConstraint(
            "config_digest ~ '^[0-9a-f]{64}$'",
            name="ck_merchant_contexts_config_digest",
        ),
        sa.CheckConstraint(
            "provider_type ~ '[^[:space:]]' AND environment ~ '[^[:space:]]' AND physical_merchant_id ~ '[^[:space:]]' AND installation_reference ~ '[^[:space:]]' AND capability_reference ~ '[^[:space:]]' AND config_revision_reference ~ '[^[:space:]]'",
            name="ck_merchant_contexts_transport_text",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_merchant_contexts_transport",
        "merchant_contexts",
        ["tenant_id", "installation_reference", "capability_reference"],
        schema=_SCHEMA,
    )
    op.create_table(
        "merchant_context_ends",
        *_review_columns(),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("replacement_context_id", sa.Uuid()),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        *_review_constraints("merchant_context_ends"),
        sa.UniqueConstraint(
            "tenant_id", "context_id", name="uq_merchant_context_ends_context"
        ),
        sa.CheckConstraint(
            "(operation = 'REVOKE' AND replacement_context_id IS NULL) OR (operation = 'ACCEPT' AND replacement_context_id IS NOT NULL AND replacement_context_id <> context_id)",
            name="ck_merchant_context_ends_operation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "context_id"],
            [
                "mod_payments.merchant_contexts.tenant_id",
                "mod_payments.merchant_contexts.id",
            ],
            ondelete="RESTRICT",
            name="fk_merchant_context_ends_context",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "replacement_context_id"],
            [
                "mod_payments.merchant_contexts.tenant_id",
                "mod_payments.merchant_contexts.id",
            ],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            name="fk_merchant_context_ends_replacement",
        ),
        schema=_SCHEMA,
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_payments.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE mod_payments.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_payments.{table} USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id())"
        )
        # The product owner can append and read reviews, never mutate history.
        op.execute(f"GRANT SELECT, INSERT ON mod_payments.{table} TO app_user")
    op.execute("""
        CREATE FUNCTION mod_payments.merchant_review_lock(
            tenant uuid, command uuid, installation text, capability text
        ) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER
        SET search_path=pg_catalog AS $$
        BEGIN
            IF current_setting('transaction_isolation') <> 'read committed'
                OR current_setting('transaction_read_only') <> 'off' THEN
                RAISE EXCEPTION 'merchant context mutations require READ WRITE READ COMMITTED'
                    USING ERRCODE='restrict_violation';
            END IF;
            -- Command identity is shared across acceptance and termination,
            -- even when they address independent transport slots. Command lock
            -- always precedes slot lock in public services and final guards.
            PERFORM pg_advisory_xact_lock(hashtextextended(
                jsonb_build_array('merchant-review-command-v1',tenant::text,command::text)::text,0));
            PERFORM pg_advisory_xact_lock(hashtextextended(
                jsonb_build_array(tenant::text,installation,capability)::text,0));
        END $$
    """)
    op.execute("""
        CREATE FUNCTION mod_payments.refuse_merchant_review_mutation() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'merchant review history is append-only'
                USING ERRCODE='restrict_violation';
        END $$
    """)
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable AFTER UPDATE OR DELETE ON mod_payments.{table} FOR EACH ROW EXECUTE FUNCTION mod_payments.refuse_merchant_review_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON mod_payments.{table} FOR EACH STATEMENT EXECUTE FUNCTION mod_payments.refuse_merchant_review_mutation()"
        )
        for suffix in ("immutable", "no_truncate"):
            op.execute(
                f"ALTER TABLE mod_payments.{table} ENABLE ALWAYS TRIGGER {table}_{suffix}"
            )
    op.execute("""
        CREATE FUNCTION mod_payments.check_merchant_context_insert() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        BEGIN
            PERFORM mod_payments.merchant_review_lock(NEW.tenant_id,NEW.command_id,NEW.installation_reference,NEW.capability_reference);
            -- VOLATILE SPI reads acquire a fresh RC snapshot AFTER the lock.
            -- RLS exposes every competing row for this exact tenant, and cross-
            -- tenant rows intentionally have an independent identity/lock scope.
            IF EXISTS (
                SELECT 1 FROM mod_payments.merchant_contexts c
                LEFT JOIN mod_payments.merchant_context_ends e
                    ON e.tenant_id=c.tenant_id AND e.context_id=c.id
                WHERE c.tenant_id=NEW.tenant_id AND c.id<>NEW.id
                  AND c.installation_reference=NEW.installation_reference
                  AND c.capability_reference=NEW.capability_reference
                  AND tstzrange(c.effective_from,least(c.effective_until,e.effective_at),'[)')
                      && tstzrange(NEW.effective_from,NEW.effective_until,'[)')
            ) THEN
                RAISE EXCEPTION 'competing accepted merchant context interval'
                    USING ERRCODE='restrict_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM mod_payments.merchant_context_ends e
                WHERE e.tenant_id=NEW.tenant_id AND e.command_id=NEW.command_id
                  AND (e.operation<>'ACCEPT' OR e.replacement_context_id<>NEW.id
                       OR e.request_fingerprint<>NEW.request_fingerprint)) THEN
                RAISE EXCEPTION 'merchant command reused for another operation'
                    USING ERRCODE='restrict_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER merchant_context_insert AFTER INSERT ON mod_payments.merchant_contexts FOR EACH ROW EXECUTE FUNCTION mod_payments.check_merchant_context_insert()"
    )
    op.execute(
        "ALTER TABLE mod_payments.merchant_contexts ENABLE ALWAYS TRIGGER merchant_context_insert"
    )
    op.execute("""
        CREATE FUNCTION mod_payments.check_merchant_context_end_insert() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        DECLARE original mod_payments.merchant_contexts%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT original FROM mod_payments.merchant_contexts
                WHERE tenant_id=NEW.tenant_id AND id=NEW.context_id;
            PERFORM mod_payments.merchant_review_lock(NEW.tenant_id,NEW.command_id,original.installation_reference,original.capability_reference);
            IF NEW.effective_at < original.effective_from OR EXISTS (
                SELECT 1 FROM mod_payments.merchant_contexts c
                WHERE c.tenant_id=NEW.tenant_id AND c.command_id=NEW.command_id
                  AND (NEW.operation<>'ACCEPT' OR NEW.replacement_context_id<>c.id
                    OR NEW.request_fingerprint<>c.request_fingerprint)
            ) THEN
                RAISE EXCEPTION 'merchant termination differs from original binding/command'
                    USING ERRCODE='restrict_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER merchant_context_end_insert AFTER INSERT ON mod_payments.merchant_context_ends FOR EACH ROW EXECUTE FUNCTION mod_payments.check_merchant_context_end_insert()"
    )
    op.execute(
        "ALTER TABLE mod_payments.merchant_context_ends ENABLE ALWAYS TRIGGER merchant_context_end_insert"
    )
    op.execute("""
        CREATE FUNCTION mod_payments.check_merchant_context_replacement() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        BEGIN
            IF NEW.operation='ACCEPT' AND NOT EXISTS (
                SELECT 1 FROM mod_payments.merchant_contexts original
                JOIN mod_payments.merchant_contexts replacement ON replacement.tenant_id=original.tenant_id
                WHERE original.tenant_id=NEW.tenant_id AND original.id=NEW.context_id
                  AND replacement.id=NEW.replacement_context_id
                  AND replacement.command_id=NEW.command_id
                  AND replacement.request_fingerprint=NEW.request_fingerprint
                  AND replacement.provider_type=original.provider_type
                  AND replacement.environment=original.environment
                  AND replacement.installation_reference=original.installation_reference
                  AND replacement.capability_reference=original.capability_reference
                  AND replacement.effective_from=NEW.effective_at
            ) THEN
                RAISE EXCEPTION 'supersession requires exact replacement context'
                    USING ERRCODE='restrict_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER merchant_context_replacement AFTER INSERT ON mod_payments.merchant_context_ends DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_payments.check_merchant_context_replacement()"
    )
    op.execute(
        "ALTER TABLE mod_payments.merchant_context_ends ENABLE ALWAYS TRIGGER merchant_context_replacement"
    )
    # Public trigger helper is invoker-only and grants no table privilege.
    # Default function EXECUTE does not expose another tenant through RLS.


def downgrade() -> None:
    # Retained review history is forward-only; an empty installation has no
    # history to preserve and may still retreat.
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_payments.{table} NO FORCE ROW LEVEL SECURITY")
        retained = (
            op.get_bind()
            .execute(sa.text(f"SELECT count(*) FROM mod_payments.{table}"))
            .scalar_one()
        )
        op.execute(f"ALTER TABLE mod_payments.{table} FORCE ROW LEVEL SECURITY")
        if retained:
            raise RuntimeError(
                "Merchant review history integrity is forward-only; "
                "use a reviewed forward migration"
            )
    for table in reversed(_TABLES):
        op.drop_table(table, schema="mod_payments")
    op.execute("DROP FUNCTION mod_payments.check_merchant_context_replacement()")
    op.execute("DROP FUNCTION mod_payments.check_merchant_context_end_insert()")
    op.execute("DROP FUNCTION mod_payments.check_merchant_context_insert()")
    op.execute("DROP FUNCTION mod_payments.refuse_merchant_review_mutation()")
    op.execute(
        "DROP FUNCTION mod_payments.merchant_review_lock(uuid, uuid, text, text)"
    )
