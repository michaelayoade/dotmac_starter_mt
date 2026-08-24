"""`dotmac_kernel.messaging` — transactional outbox + idempotent command
envelope (kernel WS3).

The kernel primitive downstream workflows (deployment intent, allocation,
commercial contracts, observed health) build on: process a command exactly once
(`process_once`), and emit a domain event atomically with its state change
(`enqueue_event` + the outbox), delivered later by a relay (slice 2). Supported
public API — see `COMPATIBILITY.md`.

`process_once` / `process_once_platform` are thin adapters over
`dotmac_kernel.idempotency`, which owns the ledger and the engine (ADR-0014).
Import the record models and `IdempotencyStatus` from
`dotmac_kernel.idempotency_models`, not from here.

Submodule-only (like `dotmac_kernel.deps`): the write helpers pull in the DB
transaction authority, so this package is NOT re-exported at the DB-free top
level. Import it directly: `from dotmac_kernel.messaging import ...`.
"""

from __future__ import annotations

from dotmac_kernel.messaging.envelope import CommandEnvelope, UnattributedCommandError
from dotmac_kernel.messaging.inbox import (
    CommandHandler,
    ProcessOutcome,
    process_once,
)
from dotmac_kernel.messaging.models import (
    OutboxEvent,
    OutboxStatus,
    PlatformOutboxEvent,
)
from dotmac_kernel.messaging.outbox import enqueue_event, enqueue_platform_event
from dotmac_kernel.messaging.platform import (
    PlatformCommandHandler,
    process_once_platform,
)
from dotmac_kernel.messaging.platform_relay import (
    ClaimedPlatformEvent,
    claim_platform_batch,
)
from dotmac_kernel.messaging.platform_worker import (
    LoggingPlatformTransport,
    PlatformDeliveryTransport,
)
from dotmac_kernel.messaging.relay import (
    ClaimedEvent,
    FailureOutcome,
    RelayPolicy,
    claim_batch,
    record_failure,
    record_success,
)
from dotmac_kernel.messaging.worker import (
    DeliveryTransport,
    LoggingTransport,
    run_forever,
    run_once,
)

__all__ = [
    # envelope
    "CommandEnvelope",
    "UnattributedCommandError",
    # inbox / idempotent processing (tenant-scoped)
    "CommandHandler",
    "ProcessOutcome",
    "process_once",
    # idempotent processing (platform-scoped)
    "PlatformCommandHandler",
    "process_once_platform",
    # outbox write side (tenant + platform)
    "enqueue_event",
    "enqueue_platform_event",
    # relay / dispatcher (slice 2)
    "RelayPolicy",
    "ClaimedEvent",
    "FailureOutcome",
    "claim_batch",
    "record_success",
    "record_failure",
    # relay worker (polling process + transport)
    "DeliveryTransport",
    "LoggingTransport",
    "run_once",
    "run_forever",
    # platform relay (separate table + dispatcher; reuses the same engine)
    "ClaimedPlatformEvent",
    "claim_platform_batch",
    "PlatformDeliveryTransport",
    "LoggingPlatformTransport",
    # persisted state
    "OutboxEvent",
    "PlatformOutboxEvent",
    "OutboxStatus",
]
