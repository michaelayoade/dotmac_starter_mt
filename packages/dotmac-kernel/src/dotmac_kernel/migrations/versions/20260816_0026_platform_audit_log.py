"""Make the online platform audit trail append-only.

``0009_platform_audit_inbox`` created ``platform_audit_events`` and granted
the online platform role SELECT, INSERT, UPDATE and DELETE.  The last two make
historical evidence mutable through an ordinary request-role connection.  This
revision completes ``platform_audit_log.v1`` by reducing that role to the two
operations the writer actually performs and removing column-level escape
paths.  ``app_admin`` retains its offline migration/retention authority.

Revision ID: 0026_platform_audit_log
Revises: 0025_session_provenance
Create Date: 2026-08-16
"""

from __future__ import annotations

from alembic import op

revision = "0026_platform_audit_log"
down_revision = "0025_session_provenance"
branch_labels = None
depends_on = None


def _remove_every_platform_api_grant() -> None:
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_audit_events "
        "FROM platform_api;"
    )
    op.execute(
        "REVOKE SELECT (id, actor_admin_id, action, entity_type, entity_id, "
        "details, created_at), INSERT (id, actor_admin_id, action, "
        "entity_type, entity_id, details, created_at), UPDATE (id, "
        "actor_admin_id, action, entity_type, entity_id, details, created_at), "
        "REFERENCES (id, actor_admin_id, action, entity_type, entity_id, "
        "details, created_at) ON public.platform_audit_events FROM platform_api;"
    )


def upgrade() -> None:
    _remove_every_platform_api_grant()
    op.execute("GRANT SELECT, INSERT ON public.platform_audit_events TO platform_api;")
    # Repeat the platform isolation here rather than relying on 0009's grant
    # history: a database can reach this revision after manual privilege drift.
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_audit_events FROM app_user;"
    )
    op.execute(
        "REVOKE SELECT (id, actor_admin_id, action, entity_type, entity_id, "
        "details, created_at), INSERT (id, actor_admin_id, action, "
        "entity_type, entity_id, details, created_at), UPDATE (id, "
        "actor_admin_id, action, entity_type, entity_id, details, created_at), "
        "REFERENCES (id, actor_admin_id, action, entity_type, entity_id, "
        "details, created_at) ON public.platform_audit_events FROM app_user;"
    )


def downgrade() -> None:
    _remove_every_platform_api_grant()
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON public.platform_audit_events "
        "TO platform_api;"
    )
