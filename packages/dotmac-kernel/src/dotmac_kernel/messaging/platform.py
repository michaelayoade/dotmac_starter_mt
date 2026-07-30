"""Platform-scoped idempotent command processing (kernel — platform variant of
the tenant-scoped `inbox.process_once`).

A platform command has NO tenant, so its idempotency key is `command_id` alone
(globally unique). `process_once_platform` runs a handler AT MOST ONCE per
`command_id`: the first delivery runs it and records a `PlatformInboxRecord` with
its result; a later delivery replays that result without re-running. Same
atomicity + concurrency guarantees as the tenant-scoped version (shared
transaction, SAVEPOINT rollback of the racing loser via `conflict_savepoint`),
and the same transaction-authority contract (RECEIVES a `Session`, only
add/flush).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.messaging.inbox import ProcessOutcome
from dotmac_kernel.messaging.models import InboxStatus, PlatformInboxRecord

# A platform handler applies the command's effect and returns a JSON-serializable
# result (or None). No envelope: platform commands carry no tenant context.
PlatformCommandHandler = Callable[[Session], "Mapping[str, object] | None"]


def _lookup(db: Session, command_id: str) -> PlatformInboxRecord | None:
    return db.execute(
        select(PlatformInboxRecord).where(PlatformInboxRecord.command_id == command_id)
    ).scalar_one_or_none()


def process_once_platform(
    db: Session,
    *,
    command_id: str,
    command_type: str,
    handler: PlatformCommandHandler,
    correlation_id: str | None = None,
) -> ProcessOutcome:
    """Process a platform command at most once per `command_id`. Returns a
    `ProcessOutcome` whose `was_duplicate` is True when a prior result was
    replayed instead of running `handler`."""
    existing = _lookup(db, command_id)
    if existing is not None:
        return ProcessOutcome(command_id, "duplicate", dict(existing.result or {}))

    try:
        with conflict_savepoint(db):
            result = dict(handler(db) or {})
            db.add(
                PlatformInboxRecord(
                    command_id=command_id,
                    command_type=command_type,
                    status=InboxStatus.PROCESSED.value,
                    result=result,
                    correlation_id=correlation_id,
                )
            )
            db.flush()
    except IntegrityError:
        winner = _lookup(db, command_id)
        return ProcessOutcome(
            command_id,
            "duplicate",
            dict(winner.result or {}) if winner is not None else {},
        )

    return ProcessOutcome(command_id, "processed", result)


__all__ = ["PlatformCommandHandler", "process_once_platform"]
