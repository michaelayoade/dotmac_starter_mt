"""Execution policy — the operational numbers, typed and injected.

Retry caps, lease durations and staleness thresholds are DEPLOYMENT decisions,
not properties of the engine. Hardcoding them means an operator who needs a
90-second lease edits library code, and a module that ships one set of numbers
for every provider is wrong for all of them: a webhook fan-out and a nightly
bulk poll do not want the same backoff.

So they arrive through one frozen, validated object rather than as loose
keyword defaults scattered across call sites. Validation is at construction —
`max_attempts=0` refused when the policy is built beats it discovered when a
delivery dead-letters on its first attempt.

The DEFAULTS below are Sub's production values, kept so the port behaves as the
source did unless a deployment says otherwise.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_POLICY",
    "DEFAULT_POLICY_DIGEST",
    "EXECUTION_POLICY_SCHEMA",
    "ExecutionPolicy",
    "execution_policy_digest",
]

EXECUTION_POLICY_SCHEMA: Final[str] = "dotmac.integration.execution-policy/v1"

_MIN_ATTEMPTS: Final[int] = 1
_MAX_ATTEMPTS: Final[int] = 20


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Operational knobs for one deployment of the control plane."""

    #: Attempts before a retryable delivery is dead-lettered.
    max_attempts: int = 10
    #: First retry delay; doubles per attempt.
    base_delay_seconds: int = 60
    #: Ceiling. Beyond this a queue is not retrying, it is hoarding.
    max_backoff_seconds: int = 8 * 60 * 60
    #: How long a worker holds a delivery. Too long strands work when a worker
    #: dies; too short lets a slow provider call be double-sent.
    lease_seconds: int = 300
    #: A checkpoint that has not advanced in this long is reported stale.
    stale_checkpoint_after_seconds: int = 24 * 60 * 60

    def __post_init__(self) -> None:
        if not _MIN_ATTEMPTS <= self.max_attempts <= _MAX_ATTEMPTS:
            raise ValueError(
                f"max_attempts={self.max_attempts} is outside "
                f"[{_MIN_ATTEMPTS}, {_MAX_ATTEMPTS}]. Zero would dead-letter "
                "every delivery on its first attempt; an unbounded value makes "
                "a permanently failing delivery immortal"
            )
        if self.base_delay_seconds < 1:
            raise ValueError("base_delay_seconds must be at least 1")
        if self.max_backoff_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_backoff_seconds must be at least base_delay_seconds, or "
                "the ceiling silently cancels the curve"
            )
        if self.lease_seconds < 1:
            raise ValueError(
                "lease_seconds must be at least 1 — a zero lease is no claim "
                "at all, and two dispatchers would both call the provider"
            )
        if self.stale_checkpoint_after_seconds < 0:
            raise ValueError("stale_checkpoint_after_seconds must not be negative")


#: Sub's production values. Used when a caller supplies no policy, so the port
#: behaves as the source did by default.
DEFAULT_POLICY: Final[ExecutionPolicy] = ExecutionPolicy()


def execution_policy_digest(policy: ExecutionPolicy) -> str:
    """Content-bind the exact module policy without exposing loose wire knobs."""

    if not isinstance(policy, ExecutionPolicy):
        raise TypeError("policy must be an ExecutionPolicy")
    document = {
        "schema": EXECUTION_POLICY_SCHEMA,
        "base_delay_seconds": policy.base_delay_seconds,
        "lease_seconds": policy.lease_seconds,
        "max_attempts": policy.max_attempts,
        "max_backoff_seconds": policy.max_backoff_seconds,
        "stale_checkpoint_after_seconds": policy.stale_checkpoint_after_seconds,
    }
    payload = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


DEFAULT_POLICY_DIGEST: Final[str] = execution_policy_digest(DEFAULT_POLICY)
