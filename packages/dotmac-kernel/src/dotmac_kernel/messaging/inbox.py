"""Idempotent command processing (kernel WS3).

`process_once` runs a command's handler AT MOST ONCE per `(tenant_id,
command_id)`: the first delivery runs the handler and records an `InboxRecord`
with its result; a later delivery of the same command finds that record and
replays the result WITHOUT re-running. The record insert shares the caller's
transaction, so the effect and the dedup marker commit atomically together.

Concurrency: two racing deliveries can both find no record and both run the
handler; the loser's `INSERT` violates `uq_inbox_records_tenant_command_id`,
its work is rolled back to a SAVEPOINT (`conflict_savepoint`), and it returns the
winner's recorded result — so the effect still happens exactly once.

Follows the kernel transaction-authority rule: RECEIVES a `Session`, never builds
one; does `add`/`flush`, never `commit`/`rollback` (the request boundary owns
that).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.messaging.models import InboxRecord, InboxStatus

# A handler applies the command's effect and returns a JSON-serializable result
# (or None) recorded for idempotent replay. It runs inside the caller's
# transaction and must only add/flush — never commit.
CommandHandler = Callable[[Session, CommandEnvelope], Mapping[str, object] | None]


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """The result of `process_once`. `status` is `"processed"` when the handler
    ran this call, or `"duplicate"` when a prior processing was replayed. `result`
    is the handler's recorded result either way."""

    command_id: str
    status: str
    result: Mapping[str, object]

    @property
    def was_duplicate(self) -> bool:
        return self.status == "duplicate"


def _lookup(db: Session, tenant_id: UUID, command_id: str) -> InboxRecord | None:
    return db.execute(
        select(InboxRecord).where(
            InboxRecord.tenant_id == tenant_id,
            InboxRecord.command_id == command_id,
        )
    ).scalar_one_or_none()


def process_once(
    db: Session, envelope: CommandEnvelope, handler: CommandHandler
) -> ProcessOutcome:
    """Process `envelope` at most once. Returns a `ProcessOutcome` whose
    `was_duplicate` is True when a prior result was replayed instead of running
    `handler`."""
    existing = _lookup(db, envelope.tenant_id, envelope.command_id)
    if existing is not None:
        return ProcessOutcome(
            envelope.command_id, "duplicate", dict(existing.result or {})
        )

    try:
        with conflict_savepoint(db):
            result = dict(handler(db, envelope) or {})
            db.add(
                InboxRecord(
                    tenant_id=envelope.tenant_id,
                    command_id=envelope.command_id,
                    command_type=envelope.command_type,
                    status=InboxStatus.PROCESSED.value,
                    result=result,
                    correlation_id=envelope.correlation_id,
                )
            )
            db.flush()
    except IntegrityError:
        # A concurrent delivery won the (tenant_id, command_id) race; this call's
        # handler effects rolled back with the SAVEPOINT. Replay the winner.
        winner = _lookup(db, envelope.tenant_id, envelope.command_id)
        return ProcessOutcome(
            envelope.command_id,
            "duplicate",
            dict(winner.result or {}) if winner is not None else {},
        )

    return ProcessOutcome(envelope.command_id, "processed", result)


__all__ = ["CommandHandler", "ProcessOutcome", "process_once"]
