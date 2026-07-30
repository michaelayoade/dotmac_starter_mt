"""`dotmac_kernel.messaging` — transactional outbox/inbox + idempotent command
envelope (kernel WS3).

The kernel primitive downstream workflows (deployment intent, allocation,
commercial contracts, observed health) build on: process a command exactly once
(`process_once` + the inbox ledger), and emit a domain event atomically with its
state change (`enqueue_event` + the outbox), delivered later by a relay
(slice 2). Supported public API — see `COMPATIBILITY.md`.

Submodule-only (like `dotmac_kernel.deps`): the write helpers pull in the DB
transaction authority, so this package is NOT re-exported at the DB-free top
level. Import it directly: `from dotmac_kernel.messaging import ...`.
"""

from __future__ import annotations

from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.inbox import (
    CommandHandler,
    ProcessOutcome,
    process_once,
)
from dotmac_kernel.messaging.models import (
    InboxRecord,
    InboxStatus,
    OutboxEvent,
    OutboxStatus,
)
from dotmac_kernel.messaging.outbox import enqueue_event

__all__ = [
    # envelope
    "CommandEnvelope",
    # inbox / idempotent processing
    "CommandHandler",
    "ProcessOutcome",
    "process_once",
    # outbox write side
    "enqueue_event",
    # persisted state
    "InboxRecord",
    "InboxStatus",
    "OutboxEvent",
    "OutboxStatus",
]
