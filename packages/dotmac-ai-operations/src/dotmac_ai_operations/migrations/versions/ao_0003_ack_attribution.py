"""Expand ``ai_insights`` with a typed acknowledgement attribution (additive
only).

Michael's ruling (round 13/14): a row that pairs AUTHORITATIVE typed
evidence (``ao_0002_insight_evidence_binding``) with an actor/time an
N-1 writer can silently overwrite makes a false HISTORICAL claim, not
merely a stale display one — actor and time are part of the
acknowledgement's integrity envelope, the same way evidence is.
Attribution gets the IDENTICAL shadowing treatment evidence already got:
a namespaced, typed actor identity travelling together in new, additive
columns the older version's code has no reference to.

This migration is EXPAND-ONLY, by the same explicit ruling
``ao_0002`` followed:

- ``acknowledged_by_ref``/``acknowledged_at`` are NOT renamed, NOT
  dropped, and NOT backfilled. They are preserved exactly as published
  (``ao_0001_ai_operations``, 0.1.0a1), so an application version rolled
  back to before this change still reads and writes them correctly
  against the expanded schema. UNLIKE the evidence case, these two
  columns are the SAME columns both an N-1 writer and the current writer
  write — there is no separate legacy pair being shadowed by a new pair
  of the identical shape. That is deliberate and unavoidable: retrofitting
  a "legacy" rename onto columns published in ``ao_0001`` would break the
  very N-1 round-trip this migration exists to preserve.
- Four new columns are ADDED, all nullable: ``attribution_actor_namespace``,
  ``attribution_actor_type``, ``attribution_actor_ref``,
  ``attribution_acknowledged_at``. These are
  ``AIAcknowledgementAttribution``'s fields. From this migration forward,
  ``service.acknowledge_insight`` writes the CURRENT writer's attribution
  only into these four columns, as a full binding —
  ``acknowledged_by_ref``/``acknowledged_at`` stop being read as
  authoritative and are reclassified as legacy-descriptive: still present,
  still written every call (for N-1 round-trip correctness), but no longer
  the source of truth for who acknowledged a row or when.
- Existing rows are NOT touched. Their ``acknowledged_by_ref``/
  ``acknowledged_at`` keep whatever an old writer already put there; their
  four new attribution columns are NULL. That is not a gap to backfill: no
  trustworthy typed actor identity was ever captured for those rows, and
  synthesising one now would fabricate a historical claim nobody actually
  made — a fabricated attribution is worse than an honest NULL.

NO CHECK CONSTRAINT enforcing "all four attribution columns together or
all four NULL" is added here, and that omission is deliberate, not an
oversight to revisit casually. The service-layer writer guarantee (`service
.acknowledge_insight` always writes all four together or all four `None`)
is a claim about THIS repository's code, not a proof about what a real N-1
database rehearsal would show under genuine concurrent writers — that
rehearsal was investigated and found NOT reachable from this repository's
ordinary CI today (the private registry read path this package would need
to install the actual published N-1 artifact is gated to a `main`-only,
`workflow_dispatch`-only protected environment; see the investigation
recorded against round 13/14). Landing a CHECK constraint with a comment
admitting its premise is unverified would still make the database enforce
an unproven assumption — documenting the risk is not the same as removing
it. The constraint is deferred to a focused SUCCESSOR migration, to be
added once that evidence exists (either the registry-reachability gap is
deliberately closed, or an equally credible oracle — e.g. a verified
rebuild from the published ``dotmac-ai-operations-v0.1.0a1`` tag's source —
is actually built and run, not merely proposed).

Revision ID: ao_0003_ack_attribution
Revises: ao_0002_insight_evidence_binding
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ao_0003_ack_attribution"
down_revision = "ao_0002_insight_evidence_binding"
branch_labels = None
depends_on = None

_SCHEMA = "mod_aiops"
_TABLE = "ai_insights"


def upgrade() -> None:
    # Additive only: `acknowledged_by_ref`/`acknowledged_at` are untouched
    # (not renamed, not dropped, not backfilled) — see module docstring.
    op.add_column(
        _TABLE,
        sa.Column("attribution_actor_namespace", sa.String(120)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("attribution_actor_type", sa.String(120)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("attribution_actor_ref", sa.String(240)),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("attribution_acknowledged_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    # Drops only what this migration added. `acknowledged_by_ref`/
    # `acknowledged_at` were never touched by `upgrade()`, so there is
    # nothing to restore for them.
    op.drop_column(_TABLE, "attribution_acknowledged_at", schema=_SCHEMA)
    op.drop_column(_TABLE, "attribution_actor_ref", schema=_SCHEMA)
    op.drop_column(_TABLE, "attribution_actor_type", schema=_SCHEMA)
    op.drop_column(_TABLE, "attribution_actor_namespace", schema=_SCHEMA)
