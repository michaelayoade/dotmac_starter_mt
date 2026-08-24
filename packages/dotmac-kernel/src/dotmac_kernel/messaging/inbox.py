"""Idempotent command processing (kernel WS3) — the transport-delivery spelling
of `dotmac_kernel.idempotency`.

`process_once` runs a command's handler AT MOST ONCE per `(tenant_id,
command_id)`: the first delivery runs the handler and records the result; a
later delivery of the same command replays that result WITHOUT re-running.

**This module owns no store and no engine** (ADR-0014). It is a thin adapter
that names the transport case in `execute_once`'s vocabulary — `scope="inbox"`,
`key` = the transport-generated `command_id`, and no fingerprint, because the
sender generated the id and the caller therefore asserts it identifies the
delivery on its own. Atomicity, the SAVEPOINT-based race handling and the
transaction-authority contract all live in the owner; see its docstring.

The signature and `ProcessOutcome` shape are unchanged from WS3 so existing
consumers need no source change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from dotmac_kernel.idempotency import execute_once
from dotmac_kernel.idempotency_models import INBOX_SCOPE
from dotmac_kernel.messaging.envelope import CommandEnvelope
from dotmac_kernel.source_applications import active_source_applications

# A handler applies the command's effect and returns a JSON-serializable result
# (or None) recorded for idempotent replay. It runs inside the caller's
# transaction and must only add/flush — never commit.
CommandHandler = Callable[[Session, CommandEnvelope], Mapping[str, object] | None]


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """The result of `process_once`. `status` is `"processed"` when the handler
    ran this call, or `"duplicate"` when a prior processing was replayed.
    `result` is the handler's recorded result either way."""

    command_id: str
    status: str
    result: Mapping[str, object]

    @property
    def was_duplicate(self) -> bool:
        return self.status == "duplicate"


def process_once(
    db: Session, envelope: CommandEnvelope, handler: CommandHandler
) -> ProcessOutcome:
    """Process `envelope` at most once. Returns a `ProcessOutcome` whose
    `was_duplicate` is True when a prior result was replayed instead of running
    `handler`.

    The issuing application is checked against this deployment's accepted set
    BEFORE the idempotency ledger is touched, so a command from an application
    this deployment does not talk to is refused rather than recorded as
    processed — recording it first would make the refusal permanent on replay
    and would let an unaccepted issuer consume a `command_id`.

    Membership is exact. `dotmac_sub_staging` does not pass as `dotmac_sub`,
    and there is no wildcard entry that would let one line re-open everything
    the registry exists to close.
    """
    active_source_applications().require(envelope.issuer())
    outcome = execute_once(
        db,
        tenant_id=envelope.tenant_id,
        scope=INBOX_SCOPE,
        key=envelope.command_id,
        operation=lambda session: handler(session, envelope),
        operation_name=envelope.command_type,
        correlation_id=envelope.correlation_id,
    )
    return ProcessOutcome(
        envelope.command_id,
        "duplicate" if outcome.replayed else "processed",
        outcome.result,
    )


__all__ = ["CommandHandler", "ProcessOutcome", "process_once"]
