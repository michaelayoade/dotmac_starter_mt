"""Verify the outbox relay this module has always enqueued into. No DDL.

`dotmac_approvals.outbox.emit_tenant_events` calls
`dotmac_kernel.messaging.outbox.enqueue_event` and `emit_platform_events` calls
`enqueue_platform_event`, so this module writes `public.outbox_events` and
`public.platform_outbox_events` at REQUEST time. `ap_0001` creates neither: the
approval tables live in `mod_approvals`, and the relay is the kernel's.

Until kernel `0.1.0a67` there was no name to declare. An adopter running its
own lineage installed this module, passed the composed gate, the namespace gate
and the live-catalog gate, migrated cleanly, and then took an `UndefinedTable`
on the first approval decision that emitted an event — at request time, in
production, with the approval transaction rolling back with it.

Found by building the facility-to-prerequisite guard, not by the sweep that
produced the two fixes before it: `dotmac-integration` and
`dotmac-entitlement-allocation` were found by grepping the IDEMPOTENCY
facility, and nobody had grepped this one. That is the argument for a guard
that enumerates kernel entry points instead of the ones somebody remembered.

## Why the whole spec, when this module only enqueues

`outbox_relay.v1` verifies the tables, the claim/settle function pair per
plane, the claim and reclaim indexes, and the dispatcher role's privileges.
This module calls none of the claiming half — it writes rows and nothing else.
It is still the right requirement: an event this module enqueues into a
database with no relay is never delivered, so a "table only" dependency would
be satisfied by a deployment where approvals silently stop reaching anyone.
The kernel publishes one name for the whole facility (`messaging.platform_relay`
imports `RelayPolicy` and `_backoff_seconds` from `messaging.relay`, so the
halves are not separable in code either), and this module needs the facility.

## COMMON, not plane-specific

Both planes enqueue: the tenant plane through `enqueue_event`, the control
plane through `enqueue_platform_event`, and this module supports either alone
or both (`supported_plane_sets`). One name covers both ledgers, so a
PLATFORM-only Vendor CP install needs it exactly as much as a tenant one.

## Why a new revision rather than an edit to `ap_0001`

`ap_0001` shipped in `dotmac-approvals-v0.1.0a1` through `-v0.1.0a4`. Those
bytes have run in databases this repository does not own, so they are history;
editing them would make the checked-in lineage disagree with what executed, in
the one direction no gate can observe
(`tests/architecture/test_released_migrations.py`).

Revision ID: ap_0002_outbox_relay
Revises: ap_0001_approvals
Create Date: 2026-08-16
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ap_0002_outbox_relay"
down_revision = "ap_0001_approvals"
branch_labels = None

# COMMON: see the plane note above. The other two lists are written out so the
# reader sees a decision rather than an omission.
COMMON_REQUIRES = ("outbox_relay.v1",)
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

# `ap_0001` already declares this module's OTHER effects
# (`module_database_roles.v1`, `tenant_scope_catalog.v1`) and verifies them at
# its own head. Re-verifying them here would prove nothing new and would give
# two revisions an opinion about one effect.
#
# No `module=`: both plane lists are empty, and passing it would ask
# `selected_module_planes` for a selection this revision does not consult.
depends_on = resolve_depends_on(COMMON_REQUIRES)


def upgrade() -> None:
    """Prove the relay, change nothing.

    Deliberately the entire body. Deploy is the last moment at which a missing
    relay is a failed migration rather than a failed approval in production,
    and `require_prerequisites` inspects the live catalog — so a stamped,
    aliased or half-supplied provider (tables present, dispatcher role missing;
    functions present without an empty `search_path`) fails here.
    """
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    """Nothing to undo: `upgrade` created no object and wrote no row.

    Not `raise NotImplementedError`. Refusing would make the `ap` lineage
    undowngradable past a revision that changed nothing, and the live canaries
    downgrade this lineage after every run.
    """
