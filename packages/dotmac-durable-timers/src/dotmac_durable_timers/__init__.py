"""Durable timer mechanics composed independently into each application."""

from dotmac_durable_timers.health import JobDeadLetter, JobHealthSink, LoggingHealthSink
from dotmac_durable_timers.jobs import JobOutcome, JobRunContext
from dotmac_durable_timers.manifest import module
from dotmac_durable_timers.migrations import versions_dir
from dotmac_durable_timers.service import (
    AcceptanceOutcome,
    AcceptanceResult,
    CancelOutcome,
    CancelResult,
    ScheduleResult,
    TimerError,
    TimerIdentity,
    TimerOutput,
    TimerSnapshot,
    TimerTrigger,
    accept_trigger,
    cancel_timer,
    current_timer,
    purge_history,
    schedule_timer,
)

__version__ = "0.1.0a3"

__all__ = [
    "AcceptanceOutcome",
    "AcceptanceResult",
    "CancelOutcome",
    "CancelResult",
    "JobDeadLetter",
    "JobHealthSink",
    "JobOutcome",
    "JobRunContext",
    "LoggingHealthSink",
    "ScheduleResult",
    "TimerError",
    "TimerIdentity",
    "TimerOutput",
    "TimerSnapshot",
    "TimerTrigger",
    "__version__",
    "accept_trigger",
    "cancel_timer",
    "current_timer",
    "module",
    "purge_history",
    "schedule_timer",
    "versions_dir",
]
