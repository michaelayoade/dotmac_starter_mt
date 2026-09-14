"""Expand ``ai_insights`` with a typed evidence binding (additive only).

``ao_0001_ai_operations`` shipped in ``dotmac-ai-operations 0.1.0a1`` and is
immutable. Its ``ai_insights.action_evidence_ref`` is a bare locator string
with no namespace, content digest, or media type — a locator that could be
relocated or reinterpreted with nothing to notice the artifact changed.
Michael's ruling (recorded against this package's ``AIEvidenceBinding``
introduction) requires every evidence input affecting a decision or owner
command to carry a typed binding: a locator namespace, an immutable content
digest, and a media type/shape.

This migration is EXPAND-ONLY, by explicit ruling:

- ``action_evidence_ref`` is NOT renamed, NOT dropped, and NOT backfilled.
  It is preserved exactly as published, so an application version rolled
  back to before this change still reads and writes it correctly against
  the expanded schema — that round-trip is the actual test of "expand
  only", and a rename would have broken it for any reader still bound to
  the ``ao_0001`` contract.
- Four new columns are ADDED, all nullable: ``action_evidence_locator_
  namespace``, ``action_evidence_locator_ref``, ``action_evidence_content_
  digest``, ``action_evidence_media_type``. These are ``AIEvidenceBinding``'s
  fields. From this migration forward, ``service.acknowledge_insight``
  writes NEW evidence only into these four columns, as a full binding —
  ``action_evidence_ref`` stops receiving authoritative writes and is
  reclassified as descriptive-only metadata: still present, still
  readable, but carrying no evidentiary weight (no namespace, no digest,
  no media type ever validated against it).
- Existing rows are NOT touched. Their ``action_evidence_ref`` keeps
  whatever locator they already had; their four new binding columns are
  NULL. That is not a gap to backfill: those acknowledgements were
  captured without a digest or media type, and synthesising one now would
  fabricate an integrity claim nobody actually made — a fabricated digest
  is worse than an honest NULL. Historical rows carry no digest and never
  will.

Retiring ``action_evidence_ref`` (dropping the column once the legacy
inventory of rows actually using it reaches zero) is deliberately deferred
to a LATER migration, not this one.

Revision ID: ao_0002_insight_evidence_binding
Revises: ao_0001_ai_operations
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ao_0002_insight_evidence_binding"
down_revision = "ao_0001_ai_operations"
branch_labels = None
depends_on = None

_SCHEMA = "mod_aiops"
_TABLE = "ai_insights"


def upgrade() -> None:
    # Additive only: `action_evidence_ref` is untouched (not renamed, not
    # dropped, not backfilled) — see module docstring.
    op.add_column(
        _TABLE,
        sa.Column("action_evidence_locator_namespace", sa.String(120)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("action_evidence_locator_ref", sa.String(240)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("action_evidence_content_digest", sa.String(64)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("action_evidence_media_type", sa.String(120)),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    # Drops only what this migration added. `action_evidence_ref` was
    # never touched by `upgrade()`, so there is nothing to restore for it.
    op.drop_column(_TABLE, "action_evidence_media_type", schema=_SCHEMA)
    op.drop_column(_TABLE, "action_evidence_content_digest", schema=_SCHEMA)
    op.drop_column(_TABLE, "action_evidence_locator_ref", schema=_SCHEMA)
    op.drop_column(_TABLE, "action_evidence_locator_namespace", schema=_SCHEMA)
