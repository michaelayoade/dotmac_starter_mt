"""Typed results: a read is ``Ok`` or ``Unreadable``, never a silent empty."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T", covariant=True)


class Reason(str, Enum):
    """Closed set of reasons a read could not be trusted."""

    CONNECTION_FAILED = "connection_failed"
    OLT_BUSY = "olt_busy"
    LOGIN_REFUSED = "login_refused"
    AUTH_REJECTED = "auth_rejected"
    PROMPT_TIMEOUT = "prompt_timeout"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    NEEDS_ARGUMENTS = "needs_arguments"
    UNKNOWN_COMMAND = "unknown_command"
    PARAMETER_ERROR = "parameter_error"
    INSUFFICIENT_PRIVILEGE = "insufficient_privilege"
    BOARD_TYPE_ERROR = "board_type_error"
    TRUNCATED = "truncated"
    ROWS_UNPARSED = "rows_unparsed"
    UNRECOGNIZED_RESPONSE = "unrecognized_response"
    UNSUPPORTED_ON_PROFILE = "unsupported_on_profile"
    UNVERIFIED_TEMPLATE = "unverified_template"


@dataclass(frozen=True, slots=True)
class Evidence:
    """What produced a result. Never carries a secret or raw device output."""

    profile_id: str
    profile_digest: str
    template_id: str
    rendered_command_redacted: str
    duration_ms: int
    transcript_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class Unreadable:
    reason: Reason
    detail: str
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class OutcomeUnknown:
    """A command that may or may not have taken effect. Never success."""

    detail: str
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class Unsupported:
    """The profile does not declare this operation. Fails closed.

    There is no fallback to another vendor's, family's or model's syntax: an
    operation the resolved profile does not claim is simply not available.
    """

    operation: str
    profile_id: str
    detail: str


__all__ = [
    "Evidence",
    "Ok",
    "OutcomeUnknown",
    "Reason",
    "Unreadable",
    "Unsupported",
]
