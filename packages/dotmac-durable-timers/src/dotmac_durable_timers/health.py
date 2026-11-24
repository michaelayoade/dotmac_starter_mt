"""The health port: how a dead-lettered job is raised as a platform-health
observation without this module importing the platform-health module.

Modules never import each other (ADR-0006 s2).  The assembly binds an
implementation under ``providers["durable_timers.health_sink"]``; the runner
refuses to start without one, because a dead letter that nothing raises is
exactly the silent failure this runner exists to remove.  ``LoggingHealthSink``
exists for an assembly that deliberately chooses log-only and says so.

Implementations MUST be idempotent on ``event_id``: the dead-letter hook can
run twice for one event (it is retried when its own transaction cannot commit).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

HEALTH_SINK_PROVIDER_KEY: Final[str] = "durable_timers.health_sink"

logger = logging.getLogger("dotmac_durable_timers.health")


@dataclass(frozen=True, slots=True)
class JobDeadLetter:
    """Everything a health observation needs; deliberately no payload, no
    handler message and no exception text (only its type name)."""

    event_id: UUID
    tenant_id: UUID
    module_code: str
    job_code: str
    generation: int | None
    attempts: int
    error: str
    observed_at: datetime


class JobHealthSink(Protocol):
    def job_dead_lettered(self, dead_letter: JobDeadLetter) -> None: ...


class LoggingHealthSink:
    """Log-only sink: an explicit, visible choice, never a default."""

    def job_dead_lettered(self, dead_letter: JobDeadLetter) -> None:
        logger.error(
            "job dead-lettered module=%s job=%s tenant=%s generation=%s "
            "attempts=%s event=%s error=%s",
            dead_letter.module_code,
            dead_letter.job_code,
            dead_letter.tenant_id,
            dead_letter.generation,
            dead_letter.attempts,
            dead_letter.event_id,
            dead_letter.error,
        )


__all__ = [
    "HEALTH_SINK_PROVIDER_KEY",
    "JobDeadLetter",
    "JobHealthSink",
    "LoggingHealthSink",
]
