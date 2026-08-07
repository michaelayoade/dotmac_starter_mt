"""Backfill entitlement grants for the capabilities that become ENFORCED here.

Assembly lineage continuation (a003 → a004). No schema change: this migration
moves DATA, and it exists because `require_capability` (module control-plane
directive step 4) starts gating two modules in the same release.

## Why a backfill is required rather than optional

`is_entitled` is deny-by-default: no grant row means `not_granted`, which means
denied. Turning enforcement on for a capability that already has live users
would therefore remove the feature from every existing tenant until an operator
granted it one by one — an outage dressed up as a policy change.

So enforcement and backfill ship together, in the same release: every tenant
that exists at this revision keeps exactly what it had, and only tenants created
AFTER it need an explicit grant. `source='backfill:a004'` records that
provenance, so an operator reviewing the grant table can tell an inherited grant
from a deliberate one — the alternative, an unmarked grant, is indistinguishable
from a decision someone made.

## Which codes

`custom_fields.use` — declared since WS1 and now enforced.
`template_studio.use` — declared and enforced in the same release. Included even
though the module is new, because a tenant that ALREADY had the module's routes
mounted (an operator who deployed the M1 commit before this one) would otherwise
lose them; granting a code nobody has used yet is harmless, and the asymmetry of
guessing wrong is not.

Idempotent: `ON CONFLICT DO NOTHING` against the natural key, so a re-run — or a
deployment where an operator already granted a code by hand — neither duplicates
a row nor overwrites a deliberate `granted=false` revocation. That last property
is the important one: a tenant whose entitlement was deliberately REVOKED must
not be silently re-granted by a backfill.

Revision ID: a004_backfill_capability_grants
Revises: a003_revocation_lists
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a004_backfill_capability_grants"
down_revision = "a003_revocation_lists"
branch_labels = None
# CROSS-LINEAGE ordering, and the reason this migration needs it: the table it
# writes into, `tenant_entitlement_grants`, belongs to the KERNEL lineage
# (`0010_tenant_entitlements`). `down_revision` only orders within an owner's
# own chain, so without this the assembly lineage could be — and was — upgraded
# ahead of kernel 0010, and the backfill failed on a table that did not exist
# yet. `depends_on` is the D1 rule for exactly this: a dependency across owners
# is a declared edge, never a spliced chain.
depends_on = ("0010_tenant_entitlements",)

# The capabilities this release begins ENFORCING. Adding a code here without
# also adding its `require_capability` guard grants something nothing checks;
# adding a guard without adding the code here removes a live feature from every
# existing tenant. They travel together.
_BACKFILLED = ("custom_fields.use", "template_studio.use")


def _backfill(code: str) -> str:
    """The INSERT for one capability code.

    A helper returning SQL rather than an f-string at the call site: the two
    statements below are then LITERAL arguments a reader (and a scanner) can see
    whole, and the code values come from `_BACKFILLED` — this module's own
    constants — never from input.
    """
    return (
        "INSERT INTO tenant_entitlement_grants "
        "(id, tenant_id, capability_code, granted, limits, source, "
        " created_at, updated_at) "
        "SELECT gen_random_uuid(), t.id, :code, true, '{}'::jsonb, "
        "       'backfill:a004', now(), now() "
        "FROM tenants t "
        "ON CONFLICT (tenant_id, capability_code) DO NOTHING"
    )


def upgrade() -> None:
    for code in _BACKFILLED:
        op.execute(sa.text(_backfill(code)).bindparams(code=code))


def downgrade() -> None:
    # Remove ONLY the rows this migration created. A grant an operator made
    # deliberately carries a different `source` (or none) and must survive a
    # downgrade — deleting by capability code alone would discard real decisions
    # to undo a data backfill.
    for code in _BACKFILLED:
        op.execute(
            sa.text(
                "DELETE FROM tenant_entitlement_grants "
                "WHERE capability_code = :code AND source = 'backfill:a004'"
            ).bindparams(code=code)
        )
