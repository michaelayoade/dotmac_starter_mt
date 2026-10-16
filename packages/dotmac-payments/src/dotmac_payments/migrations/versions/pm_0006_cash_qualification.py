"""Original provider cash qualification facts; no ledger or permission token."""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pm_0006_cash_qualification"
down_revision = "pm_0005_merchant_context"
branch_labels = None
REQUIRES = ("outbox_relay.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_payments"
_TABLE = "provider_cash_qualifications"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    text_lengths = {
        "request_fingerprint": 64,
        "evidence_owner": 160,
        "evidence_reference": 512,
        "evidence_schema_version": 80,
        "evidence_sha256": 64,
        "provider_type": 40,
        "environment": 40,
        "physical_merchant_id": 160,
        "installation_reference": 160,
        "capability_reference": 160,
        "config_revision_reference": 160,
        "config_digest": 64,
        "transaction_kind": 80,
        "transaction_id": 160,
        "checkout_reference": 120,
        "status": 40,
        "currency_code": 3,
        "source_namespace": 80,
        "source_key": 160,
        "source_version": 80,
        "provenance_fingerprint": 64,
    }
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        *(
            sa.Column(name, sa.String(length), nullable=False)
            for name, length in text_lengths.items()
        ),
        *(
            sa.Column(name, sa.DateTime(timezone=True), nullable=False)
            for name in ("verified_at", "paid_at", "observed_at")
        ),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "context_id"],
            [
                "mod_payments.merchant_contexts.tenant_id",
                "mod_payments.merchant_contexts.id",
            ],
            ondelete="RESTRICT",
            name="fk_provider_cash_context",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_provider_cash_tenant_id"),
        sa.UniqueConstraint("tenant_id", "command_id", name="uq_provider_cash_command"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_type",
            "environment",
            "physical_merchant_id",
            "transaction_kind",
            "transaction_id",
            name="uq_provider_cash_physical_transaction",
        ),
        sa.CheckConstraint(
            "amount > 0 AND minor_units BETWEEN 0 AND 6 AND amount = round(amount,minor_units)",
            name="ck_provider_cash_money",
        ),
        sa.CheckConstraint("status = 'succeeded'", name="ck_provider_cash_status"),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'", name="ck_provider_cash_currency"
        ),
        sa.CheckConstraint(
            "paid_at <= verified_at AND verified_at <= observed_at AND isfinite(paid_at) AND isfinite(verified_at) AND isfinite(observed_at)",
            name="ck_provider_cash_clocks",
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} ~ '[^[:space:]]'" for name in text_lengths),
            name="ck_provider_cash_text",
        ),
        sa.CheckConstraint(
            " AND ".join(
                f"{name} ~ '^[0-9a-f]{{64}}$'"
                for name in (
                    "request_fingerprint",
                    "evidence_sha256",
                    "config_digest",
                    "provenance_fingerprint",
                )
            ),
            name="ck_provider_cash_digests",
        ),
        schema=_SCHEMA,
    )
    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_TABLE}_tenant_isolation ON {_SCHEMA}.{_TABLE} USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT ON {_SCHEMA}.{_TABLE} TO app_user")
    op.execute("""
        CREATE FUNCTION mod_payments.provider_cash_digest(domain text, vals text[])
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SECURITY INVOKER
        SET search_path=pg_catalog AS $$
        DECLARE v text; framed bytea := ''::bytea;
        BEGIN
            FOREACH v IN ARRAY array_prepend(domain,vals) LOOP
                IF v IS NULL THEN RAISE EXCEPTION 'missing provider cash digest field'; END IF;
                framed := framed || convert_to(octet_length(convert_to(v,'UTF8'))::text || ':','UTF8') || convert_to(v,'UTF8');
            END LOOP;
            RETURN encode(sha256(framed),'hex');
        END $$
    """)
    op.execute("""
        CREATE FUNCTION mod_payments.provider_cash_source_lock(tenant uuid, namespace text, source_key text)
        RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        BEGIN
            IF current_setting('transaction_isolation') <> 'read committed' OR current_setting('transaction_read_only') <> 'off' THEN
                RAISE EXCEPTION 'provider cash requires READ WRITE READ COMMITTED' USING ERRCODE='restrict_violation';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(jsonb_build_array('provider-cash-source-v1',tenant::text,namespace,source_key)::text,0));
        END $$
    """)
    op.execute("""
        CREATE FUNCTION mod_payments.check_provider_cash_insert() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
        DECLARE original mod_payments.merchant_contexts%ROWTYPE;
                ns text[]; capture text[]; money text; paid text; verified text; observed text;
        BEGIN
            PERFORM mod_payments.merchant_review_lock(NEW.tenant_id,NEW.command_id,NEW.installation_reference,NEW.capability_reference);
            PERFORM mod_payments.provider_cash_source_lock(NEW.tenant_id,NEW.source_namespace,NEW.source_key);
            SELECT * INTO STRICT original FROM mod_payments.merchant_contexts WHERE tenant_id=NEW.tenant_id AND id=NEW.context_id;
            IF ROW(NEW.provider_type,NEW.environment,NEW.physical_merchant_id,NEW.installation_reference,NEW.capability_reference,NEW.config_revision_reference,NEW.config_digest)
                IS DISTINCT FROM ROW(original.provider_type,original.environment,original.physical_merchant_id,original.installation_reference,original.capability_reference,original.config_revision_reference,original.config_digest)
                OR NEW.verified_at < original.effective_from
                OR (original.effective_until IS NOT NULL AND NEW.verified_at >= original.effective_until)
                OR clock_timestamp() < original.effective_from
                OR (original.effective_until IS NOT NULL AND clock_timestamp() >= original.effective_until)
                OR NEW.observed_at > clock_timestamp()
                OR EXISTS (SELECT 1 FROM mod_payments.merchant_context_ends WHERE tenant_id=NEW.tenant_id AND context_id=NEW.context_id)
            THEN RAISE EXCEPTION 'provider cash original context is not applicable' USING ERRCODE='restrict_violation'; END IF;
            ns := ARRAY[NEW.provider_type,NEW.environment,NEW.physical_merchant_id,NEW.transaction_kind];
            money := to_char(NEW.amount, 'FM999999999999990' || CASE WHEN NEW.minor_units=0 THEN '' ELSE '.' || repeat('0',NEW.minor_units) END);
            paid := to_char(NEW.paid_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00';
            verified := to_char(NEW.verified_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00';
            observed := to_char(NEW.observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00';
            capture := ARRAY[NEW.transaction_kind,NEW.transaction_id,NEW.checkout_reference,NEW.status,money,NEW.currency_code,NEW.minor_units::text,paid];
            IF NEW.source_namespace <> 'pcn1:' || mod_payments.provider_cash_digest('provider-cash-namespace-v1',ns)
                OR NEW.source_key <> NEW.transaction_id
                OR NEW.source_version <> 'pcv1:' || mod_payments.provider_cash_digest('provider-cash-economic-capture-v1',ns || capture)
                OR NEW.request_fingerprint <> mod_payments.provider_cash_digest('provider-cash-request-v1',ARRAY[NEW.command_id::text,NEW.evidence_owner,NEW.evidence_reference,NEW.evidence_schema_version,NEW.evidence_sha256])
                OR NEW.provenance_fingerprint <> mod_payments.provider_cash_digest('provider-cash-original-provenance-v1',ARRAY[NEW.tenant_id::text,NEW.command_id::text,NEW.request_fingerprint,NEW.evidence_owner,NEW.evidence_reference,NEW.evidence_schema_version,NEW.evidence_sha256,NEW.provider_type,NEW.environment,NEW.installation_reference,NEW.capability_reference,NEW.config_revision_reference,NEW.config_digest,NEW.context_id::text,verified,observed])
            THEN RAISE EXCEPTION 'provider cash economic identity/provenance mismatch' USING ERRCODE='restrict_violation'; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        f"CREATE TRIGGER provider_cash_insert AFTER INSERT ON {_SCHEMA}.{_TABLE} FOR EACH ROW EXECUTE FUNCTION mod_payments.check_provider_cash_insert()"
    )
    op.execute(
        f"CREATE TRIGGER provider_cash_immutable AFTER UPDATE OR DELETE ON {_SCHEMA}.{_TABLE} FOR EACH ROW EXECUTE FUNCTION mod_payments.refuse_merchant_review_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER provider_cash_no_truncate BEFORE TRUNCATE ON {_SCHEMA}.{_TABLE} FOR EACH STATEMENT EXECUTE FUNCTION mod_payments.refuse_merchant_review_mutation()"
    )
    for trigger in (
        "provider_cash_insert",
        "provider_cash_immutable",
        "provider_cash_no_truncate",
    ):
        op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    raise RuntimeError(
        "Provider cash qualification history is forward-only; use a reviewed forward migration"
    )
