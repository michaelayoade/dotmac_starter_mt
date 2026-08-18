"""Durable timer mechanics composed independently into each application."""

from dotmac_durable_timers.manifest import module
from dotmac_durable_timers.migrations import versions_dir
from dotmac_durable_timers.models import (
    PLATFORM_TABLES,
    SCHEMA,
    TENANT_TABLES,
    PlatformTimer,
    PlatformTimerAcceptance,
    Timer,
    TimerAcceptance,
)
from dotmac_durable_timers.service import (
    AcceptanceOutcome,
    AcceptanceResult,
    CancelOutcome,
    CancelResult,
    ScheduleResult,
    TimerError,
    TimerIdentity,
    TimerOutput,
    TimerTrigger,
    accept_trigger,
    cancel_timer,
    purge_history,
    schedule_timer,
)

__version__ = "0.1.0a1"

__all__ = [
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "AcceptanceOutcome",
    "AcceptanceResult",
    "CancelOutcome",
    "CancelResult",
    "PlatformTimer",
    "PlatformTimerAcceptance",
    "ScheduleResult",
    "Timer",
    "TimerAcceptance",
    "TimerError",
    "TimerIdentity",
    "TimerOutput",
    "TimerTrigger",
    "__version__",
    "accept_trigger",
    "cancel_timer",
    "module",
    "purge_history",
    "schedule_timer",
    "versions_dir",
]
