"""Optional adapter: approval events onto the kernel's transactional outbox.

ADR-0026 § 6 says consequences leave as outbox events and the consuming domain
runs its own guarded transition. This module supplies the write side of that,
and it is deliberately a SEPARATE import from `service`:

- `service` returns `ApprovalEvent` values and writes only into `mod_approvals`.
  Its persistence footprint is entirely its own schema, which is what keeps its
  migration's declared prerequisites honest — it needs a tenant to hang a
  foreign key on and roles to grant to, and nothing else.
- this adapter writes into the KERNEL's outbox tables (`public.outbox_events` /
  `public.platform_outbox_events`). An assembly that composes it is asserting
  those tables exist. Keeping it out of `service` means a consumer that has its
  own delivery mechanism — or none — is not forced to install the kernel's.

Both functions `add`/`flush` into the caller's session and never commit, so an
event is persisted if and only if the approval state change it describes is.
That atomicity is the whole point of an outbox: the two can never diverge.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_approvals.contracts import ApprovalEvent

# The kernel import is deliberately INSIDE the functions below, not here.
#
# `dotmac_kernel.messaging.outbox` re-exports through `messaging/__init__`,
# which reaches `inbox` → `idempotency` → `dotmac_kernel.db` — and that module
# builds an Engine from settings AT IMPORT TIME. A module-level import here
# would therefore make `import dotmac_approvals.outbox` fail wherever
# `DATABASE_URL` is unset, and would drag an engine into every process that
# merely wanted to read the approval contracts.
#
# Import-safety is a module invariant, not a preference: a manifest is imported
# by tooling, gates and test collection long before any database exists. Paying
# one function-local import keeps `dotmac_approvals` importable anywhere.


def emit_tenant_events(
    db: Session,
    *,
    tenant_id: UUID,
    events: Iterable[ApprovalEvent],
    correlation_id: str | None = None,
) -> int:
    """Enqueue tenant approval events; returns how many were written."""
    from dotmac_kernel.messaging.outbox import enqueue_event

    written = 0
    for event in events:
        enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type=event.event_type,
            payload=event.payload(),
            correlation_id=correlation_id,
        )
        written += 1
    return written


def emit_platform_events(
    db: Session,
    *,
    events: Iterable[ApprovalEvent],
    correlation_id: str | None = None,
) -> int:
    """Enqueue control-plane approval events; returns how many were written."""
    from dotmac_kernel.messaging.outbox import enqueue_platform_event

    written = 0
    for event in events:
        enqueue_platform_event(
            db,
            event_type=event.event_type,
            payload=event.payload(),
            correlation_id=correlation_id,
        )
        written += 1
    return written


__all__ = ["emit_platform_events", "emit_tenant_events"]
