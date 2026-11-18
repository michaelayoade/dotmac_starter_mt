"""Dispatch identity: the dedupe key and rendered snapshot (ADR-0006 § 5g, K-3).

Creates `communication_dispatches`: one row per outbound message identity, holding
the product-supplied idempotency key (UNIQUE per tenant), the rendered snapshot
(S6 `OutboundMessage` parts), its content fingerprint and template provenance.

Deliberately NOT a queue. The kernel outbox stays the queue; the outbox event for
a dispatch carries only `{"dispatch_id": ...}`.

Privilege posture (this is the point of the migration):
- tenant RLS with FORCE, same policy shape as `communication_deliveries` (0020);
- `app_user`: SELECT, INSERT, and column-level UPDATE on the outcome/redaction
  columns only, further constrained by a BEFORE UPDATE guard trigger (status
  allowlist, redaction-only content changes). No DELETE, no UPDATE of key, address, fingerprint or provenance;
- `outbox_dispatcher`: NOTHING (explicitly revoked here; its only door is the
  claim/settle SECURITY DEFINER functions, and rendered bodies are personal data);
- the Academy "lab worker" role concept: NOTHING (it is not granted here);
- `platform_api`: nothing (this is tenant data).

Additive. Downgrade drops the table, which is only safe while it is empty
(working down = drop on an empty table); it refuses otherwise rather than lose
the only record of which legacy keys were already sent.

Revision ID: 0032_communication_dispatches
Revises: 0031_rate_limit_buckets
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032_communication_dispatches"
down_revision = "0031_rate_limit_buckets"
branch_labels = None
depends_on = None

_TABLE = "communication_dispatches"
_JSON = postgresql.JSONB(none_as_null=True)
_FUNCTION = "communication_dispatches_guard"

# SECURITY INVOKER on purpose (the default): it runs as the CALLING role, so it
# constrains app_user and the migrator alike; SECURITY DEFINER would run as the
# BYPASSRLS owner and is deliberately not used.
#
# Status allowlist (`failed` = permanent refusal only; a transient provider
# failure leaves the dispatch `pending`, the K-2 receipt `failed` is a different,
# retryable thing):
#   pending -> sent | failed | dead | suppressed
#   dead    -> pending   (K-5 audited requeue; refused once redacted)
#   anything else is refused; an unchanged status is always fine.
# Redaction: the content columns (address, subject, text_body, html_body,
# headers, attachments) may only change to all-NULL, in one update that also
# sets redacted_at (NULL -> set) on a row whose OLD status is already terminal,
# without changing the status. redacted_at is one-way.
_GUARD_FUNCTION = f"""
CREATE FUNCTION {_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
  content_changed boolean;
  terminal constant text[] := ARRAY['sent','failed','dead','suppressed','history'];
BEGIN
  content_changed :=
    (NEW.address, NEW.subject, NEW.text_body, NEW.html_body, NEW.headers,
     NEW.attachments)
    IS DISTINCT FROM
    (OLD.address, OLD.subject, OLD.text_body, OLD.html_body, OLD.headers,
     OLD.attachments);
  IF OLD.redacted_at IS NOT NULL AND NEW.redacted_at IS DISTINCT FROM OLD.redacted_at
  THEN
    RAISE EXCEPTION 'communication_dispatches: redacted_at is one-way'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'pending'
        AND NEW.status IN ('sent','failed','dead','suppressed'))
    OR (OLD.status = 'dead' AND NEW.status = 'pending'
        AND OLD.redacted_at IS NULL)
  ) THEN
    RAISE EXCEPTION 'communication_dispatches: status % -> % is not allowed',
      OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL THEN
    IF NOT (OLD.status = ANY (terminal)) OR NEW.status <> OLD.status THEN
      RAISE EXCEPTION
        'communication_dispatches: redaction needs an already-terminal status, '
        'unchanged' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF content_changed THEN
    RAISE EXCEPTION 'communication_dispatches: content is immutable'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$fn$;
"""


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("dispatch_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("address", sa.String(320), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("headers", _JSON, nullable=True),
        sa.Column("attachments", _JSON, nullable=True),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("template_provenance", sa.String(255), nullable=False),
        sa.Column("template_revision_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "requested_available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_communication_dispatches_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'dead', 'suppressed', "
            "'history')",
            name="ck_communication_dispatches_status",
        ),
        sa.CheckConstraint(
            "status <> 'history' OR (address IS NULL AND subject IS NULL "
            "AND text_body IS NULL AND html_body IS NULL AND headers IS NULL "
            "AND attachments IS NULL AND content_fingerprint IS NULL)",
            name="ck_communication_dispatches_history_bare",
        ),
        sa.CheckConstraint(
            "status = 'history' OR content_fingerprint IS NOT NULL",
            name="ck_communication_dispatches_fingerprint",
        ),
        sa.CheckConstraint(
            "status = 'history' OR redacted_at IS NOT NULL OR address IS NOT NULL",
            name="ck_communication_dispatches_address",
        ),
        sa.CheckConstraint(
            "redacted_at IS NULL OR (address IS NULL AND subject IS NULL "
            "AND text_body IS NULL AND html_body IS NULL AND headers IS NULL "
            "AND attachments IS NULL)",
            name="ck_communication_dispatches_redacted_bare",
        ),
        sa.CheckConstraint(
            "status = 'history' OR redacted_at IS NOT NULL "
            "OR text_body IS NOT NULL OR html_body IS NOT NULL",
            name="ck_communication_dispatches_has_body",
        ),
        sa.CheckConstraint(
            "redacted_at IS NULL OR status <> 'pending'",
            name="ck_communication_dispatches_redact_terminal",
        ),
    )
    op.create_index(
        "uq_communication_dispatches_tenant_key",
        _TABLE,
        ["tenant_id", "idempotency_key"],
        unique=True,
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"REVOKE ALL ON {_TABLE} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON {_TABLE} FROM app_user;")
    op.execute(f"REVOKE ALL ON {_TABLE} FROM outbox_dispatcher;")
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO app_user;")
    op.execute(
        f"GRANT UPDATE (status, terminal_at, redacted_at, address, subject, "
        f"text_body, html_body, headers, attachments) ON {_TABLE} TO app_user;"
    )
    op.execute(_GUARD_FUNCTION)
    op.execute(
        f"CREATE TRIGGER {_TABLE}_guard BEFORE UPDATE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}();"
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Serialise against concurrent writers, and make a non-BYPASSRLS migrator
    # fail closed (an RLS-filtered emptiness check would wrongly see no rows).
    op.execute(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute("SET LOCAL row_security = off")
    if bind.execute(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")  # noqa: S608
    ).scalar():
        raise RuntimeError(
            f"refusing to drop non-empty {_TABLE}: it is the only record of "
            "dispatch identities (including imported legacy keys)"
        )
    op.execute(f"DROP TRIGGER IF EXISTS {_TABLE}_guard ON {_TABLE};")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}();")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index("uq_communication_dispatches_tenant_key", table_name=_TABLE)
    op.drop_table(_TABLE)
