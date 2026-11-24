"""Background-job declarations (Starter parity programme, surface contract 1.2).

A module declares the recurring or due-time work it owns on
``ModuleManifest.jobs`` as :class:`JobSpec` rows.  The kernel only DESCRIBES and
VALIDATES the declaration; it never runs anything.  The runner that maps a due
timer to a handler lives in ``dotmac-durable-timers`` and reads
``ModuleRegistry.jobs()``.

Author contract
===============

A handler is a plain function addressed by an import path (``"package.module:function"``
or ``"package.module.function"``).  It is called as::

    handler(session, scope, run_context) -> JobOutcome

and **must be idempotent**: delivery is at-least-once and a lease can expire
mid-run.  ``JobOutcome`` and ``run_context`` are owned by the runner; the
kernel checks only that the path resolves to a callable accepting three
positional arguments.

Cadence is exactly one of ``interval_seconds`` (a fixed gap) or ``cron`` (a
five-field expression evaluated in the TENANT timezone).  ``plane`` is
mandatory and explicit: it says which database plane the runner opens for the
handler.  ``concurrency`` is ``"single"`` (one run of a job at a time per
scope) - the only mode the contract offers.

Import-safe: pure data and ``importlib``; no engine, no I/O, no FastAPI.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from dotmac_kernel.planes import ModulePlane

JOB_CONCURRENCY_SINGLE: Final[str] = "single"
MIN_INTERVAL_SECONDS: Final[int] = 5
MAX_ATTEMPTS_CEILING: Final[int] = 100

_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_HANDLER_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*"
    r"(:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*)?$"
)
# (low, high) inclusive per cron field: minute hour day-of-month month day-of-week
_CRON_FIELDS: Final[tuple[tuple[str, int, int], ...]] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),
)


class JobSpecError(ValueError):
    """A job declaration is malformed or its handler does not resolve."""


def _validate_cron_field(name: str, text: str, low: int, high: int) -> None:
    for part in text.split(","):
        if not part:
            raise JobSpecError(f"cron {name} field has an empty list item")
        base, _, step = part.partition("/")
        if step:
            if not step.isdecimal() or int(step) < 1:
                raise JobSpecError(
                    f"cron {name} step {step!r} is not a positive integer"
                )
        if base == "*":
            continue
        start, dash, end = base.partition("-")
        for bound in (start, end) if dash else (start,):
            if not bound.isdecimal() or not low <= int(bound) <= high:
                raise JobSpecError(
                    f"cron {name} value {bound!r} is outside {low}-{high}"
                )
        if dash and int(start) > int(end):
            raise JobSpecError(f"cron {name} range {base!r} runs backwards")


def validate_cron(expression: str) -> None:
    """Refuse anything but a plain five-field cron expression."""
    fields = expression.split()
    if len(fields) != len(_CRON_FIELDS):
        raise JobSpecError(
            f"cron {expression!r} must have exactly 5 fields "
            "(minute hour day-of-month month day-of-week)"
        )
    for (name, low, high), text in zip(_CRON_FIELDS, fields, strict=True):
        _validate_cron_field(name, text, low, high)


@dataclass(frozen=True)
class JobSpec:
    """One background job a module declares it OWNS."""

    code: str
    handler: str
    plane: ModulePlane | str
    interval_seconds: int | None = None
    cron: str | None = None
    max_attempts: int = 5
    # Seconds to wait before attempt 2, 3, ...; the last value repeats.
    backoff_seconds: tuple[int, ...] = (30, 120, 600)
    concurrency: str = JOB_CONCURRENCY_SINGLE
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _CODE_RE.fullmatch(self.code):
            raise JobSpecError(
                f"job code {self.code!r} must be lowercase "
                "[a-z][a-z0-9_.-]* (max 80 characters)"
            )
        if not isinstance(self.handler, str) or not _HANDLER_RE.fullmatch(self.handler):
            raise JobSpecError(
                f"job {self.code!r} handler {self.handler!r} must be an import "
                "path 'package.module:function' (or dotted)"
            )
        try:
            plane = ModulePlane(self.plane)
        except ValueError as exc:
            raise JobSpecError(
                f"job {self.code!r} must declare plane 'tenant' or 'platform', "
                f"got {self.plane!r}"
            ) from exc
        object.__setattr__(self, "plane", plane)
        if (self.interval_seconds is None) == (self.cron is None):
            raise JobSpecError(
                f"job {self.code!r} must declare exactly one of "
                "`interval_seconds` or `cron`"
            )
        if self.interval_seconds is not None and (
            isinstance(self.interval_seconds, bool)
            or not isinstance(self.interval_seconds, int)
            or self.interval_seconds < MIN_INTERVAL_SECONDS
        ):
            raise JobSpecError(
                f"job {self.code!r} interval_seconds must be an integer >= "
                f"{MIN_INTERVAL_SECONDS}"
            )
        if self.cron is not None:
            try:
                validate_cron(self.cron)
            except JobSpecError as exc:
                raise JobSpecError(f"job {self.code!r}: {exc}") from exc
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= MAX_ATTEMPTS_CEILING
        ):
            raise JobSpecError(
                f"job {self.code!r} max_attempts must be 1-{MAX_ATTEMPTS_CEILING}"
            )
        backoff = tuple(self.backoff_seconds)
        if not backoff or any(
            isinstance(s, bool) or not isinstance(s, int) or s < 0 for s in backoff
        ):
            raise JobSpecError(
                f"job {self.code!r} backoff_seconds must be a non-empty tuple "
                "of non-negative integers"
            )
        object.__setattr__(self, "backoff_seconds", backoff)
        if self.concurrency != JOB_CONCURRENCY_SINGLE:
            raise JobSpecError(
                f"job {self.code!r} concurrency must be {JOB_CONCURRENCY_SINGLE!r}"
            )


def resolve_job_handler(spec: JobSpec) -> Callable[..., object]:
    """Import and return the handler, refusing anything not callable with
    ``(session, scope, run_context)``.  Called by the registry at boot."""
    module_path, sep, attr = spec.handler.partition(":")
    if not sep:
        module_path, _, attr = spec.handler.rpartition(".")
        if not module_path:
            raise JobSpecError(
                f"job {spec.code!r} handler {spec.handler!r} names no module"
            )
    try:
        target: object = importlib.import_module(module_path)
        for part in attr.split("."):
            target = getattr(target, part)
    except (ImportError, AttributeError) as exc:
        raise JobSpecError(
            f"job {spec.code!r} handler {spec.handler!r} does not resolve: {exc}"
        ) from exc
    if not callable(target):
        raise JobSpecError(
            f"job {spec.code!r} handler {spec.handler!r} is not callable"
        )
    try:
        inspect.signature(target).bind(object(), object(), object())
    except TypeError as exc:
        raise JobSpecError(
            f"job {spec.code!r} handler {spec.handler!r} must accept "
            f"(session, scope, run_context): {exc}"
        ) from exc
    except ValueError:  # builtins without a signature: cannot be proven, refuse
        raise JobSpecError(
            f"job {spec.code!r} handler {spec.handler!r} has no inspectable "
            "signature"
        ) from None
    return target


__all__ = [
    "JOB_CONCURRENCY_SINGLE",
    "MAX_ATTEMPTS_CEILING",
    "MIN_INTERVAL_SECONDS",
    "JobSpec",
    "JobSpecError",
    "resolve_job_handler",
    "validate_cron",
]
