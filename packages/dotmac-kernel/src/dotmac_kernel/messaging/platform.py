"""Platform-scoped idempotent command processing — the platform variant of
`inbox.process_once`, and like it a thin adapter over
`dotmac_kernel.idempotency` (ADR-0014).

A platform command has NO tenant, so its key is `command_id` alone (globally
unique within `scope="inbox"`). `process_once_platform` runs a handler AT MOST
ONCE per `command_id`: the first delivery runs it, a later delivery replays the
recorded result. Same atomicity and concurrency guarantees as the tenant-scoped
version — they are literally the same engine, against the platform catalog table.

The signature and `ProcessOutcome` shape are unchanged from WS3.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy.orm import Session

from dotmac_kernel.idempotency import execute_once_platform
from dotmac_kernel.idempotency_models import INBOX_SCOPE
from dotmac_kernel.messaging.inbox import ProcessOutcome

# A platform handler applies the command's effect and returns a JSON-serializable
# result (or None). No envelope: platform commands carry no tenant context.
PlatformCommandHandler = Callable[[Session], "Mapping[str, object] | None"]


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
    outcome = execute_once_platform(
        db,
        scope=INBOX_SCOPE,
        key=command_id,
        operation=handler,
        operation_name=command_type,
        correlation_id=correlation_id,
    )
    return ProcessOutcome(
        command_id,
        "duplicate" if outcome.replayed else "processed",
        outcome.result,
    )


__all__ = ["PlatformCommandHandler", "process_once_platform"]
