"""Verify the at-most-once ledger this module has always used. No DDL.

`dotmac_integration.idempotency.run_effect_once` delegates at-most-once
execution to `dotmac_kernel.idempotency.execute_once_platform` (hard rule 21,
ADR-0014: the ledger has exactly ONE owner and this module is not it). That
call writes `public.platform_idempotency_records` at REQUEST time, and not one
statement in `ig_0001`..`ig_0006` creates it.

So until now the dependency existed only inside a function body. An adopter
running its own lineage — ERP hosts `public.tenants` itself and structurally
cannot run kernel `0001` — installs this module, passes the composed gate, the
namespace gate and the live-catalog gate, migrates cleanly, and then dies on
`UndefinedTable` the first time a delivery is guarded. Nothing could have
caught it, because there was no name to declare. Kernel `0.1.0a66` published
one: `idempotency_ledger.v1`.

## Why a NEW revision and not an edit to `ig_0001`

`ig_0001` shipped in `dotmac-integration-v0.1.0a1`, `-v0.1.0a2` and
`-v0.1.0a3`.
Its bytes have run in databases this repository does not own, so they are
history — editing them would make the checked-in lineage disagree with what
actually executed, silently, in the one direction no gate can observe. The
verification therefore lands as its own head.
(`tests/architecture/test_released_migrations.py` is what stops the next
author reaching for the easier edit.)

`dotmac-numbering` took the other route in kernel a66 and was right to: its
`0.1.0a1` was never published, so `nu_0001` was not yet history.

## Why a migration is the right place at all

Deploy is the LAST moment at which a missing ledger is a failed migration
rather than a failed delivery in production. `require_prerequisites` inspects
the live catalog — the column contract, the unique key per plane, the
`expires_at` index and the ADR-0014 plane posture — so a stamped, aliased or
half-supplied provider fails here, loudly, with no DDL of this module's own
having run.

Revision ID: ig_0007_idempotency_ledger
Revises: ig_0006_retention
Create Date: 2026-08-15
"""

from __future__ import annotations

from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ig_0007_idempotency_ledger"
down_revision = "ig_0006_retention"
branch_labels = None

# COMMON, and the three lists are written out in full so the reader sees the
# decision rather than an omission. This module owns exactly one plane
# (`tables` is empty, `supported_plane_sets` unset), so the platform plane is
# installed atomically and no selection exists under which the ledger stops
# being needed — a plane-specific list would condition on something that cannot
# vary. It would also be unresolvable: `resolve_depends_on` needs `module=` to
# read a plane list, `module=` reads `selected_module_planes`, and an atomic
# module may not have a selection installed at all.
COMMON_REQUIRES = ("idempotency_ledger.v1",)
TENANT_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

# The assembly binds the effect to the revision that SUPPLIES it. In the
# reference assembly that is kernel `0018_idempotency_one_owner`, not the
# lineage root — bind it to `0001` and a database stopped at `0017` would order
# correctly and still have no ledger. This file names neither; that is the
# point of the indirection (`app/migration_bindings.py` in each assembly).
#
# No `module=`: see the note on the tuples above. Passing it with two empty
# plane lists would buy nothing and raise `ModulePlaneSelectionError`.
depends_on = resolve_depends_on(COMMON_REQUIRES)


def upgrade() -> None:
    """Prove the ledger, change nothing.

    Deliberately the entire body. A verification revision that also created
    something would make "did the check run?" and "did the object appear?" one
    question with one `alembic_version` row, and a future author repairing the
    DDL half would be repairing the guard too.
    """
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    """Nothing to undo: `upgrade` created no object and wrote no row.

    Not `raise NotImplementedError`. Refusing here would make the whole `ig`
    lineage undowngradable past this point over a revision that changed
    nothing, which is a worse lie than an empty body.
    """
