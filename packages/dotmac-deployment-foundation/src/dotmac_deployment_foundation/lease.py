"""``HostLease.v1`` — exclusive use of a shared target, and why it cannot be
self-granted.

Measured on `85.190.246.211`, 2026-08-30: `/var/lock` held `lvm/` and
`subsys/` and nothing else. There was **no lease mechanism at all**, while
eleven agents' worktrees and four agents' containers shared the host. "Exclusive
lease" was a sentence in a plan, and a sentence is not a lock.

## The rule that shapes this file

**A lease is not self-granted: it must reference the Platform CP authorization
run.** A holder that writes its own lease has proved only that it can write a
file. `authorization_run_id` is therefore mandatory and non-empty, and the
runner that consumes a lease checks it matches the run it is executing — so the
lease and the work are bound to the same authorization or the work does not
start.

This module OWNS no authorization state and issues nothing. It records a
reference to a decision another owner made, and refuses a record that does not
carry one. That distinction is the same one `provenance.AuthorizationReceipt`
draws, for the same reason.

## Two separate things, deliberately not merged

- The **lease** is durable and coarse: this holder has the host, for this
  window, under this authorization. It survives a process exiting.
- The **host lock** (`engine.lock.deployment_lock`) is transient and fine: this
  process is mutating right now. It is taken before apply and held through
  observation and rollback.

Collapsing them would mean either a lock that outlives its process or a lease
that a crash silently releases. Both have a failure mode; they are different
failure modes and want different mechanisms.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .errors import PreconditionFailed, SpecError

__all__ = [
    "HOST_LEASE_SCHEMA",
    "DEFAULT_LEASE_DIR",
    "HostLease",
    "load_lease",
    "write_lease",
]

HOST_LEASE_SCHEMA: Final = "HostLease.v1"

#: Root-owned, under the Deployment Control state directory. Overridable
#: because "everything by config" applies to paths too, but the default is the
#: one place a reviewer knows to look.
DEFAULT_LEASE_DIR: Final = os.environ.get(
    "DOTMAC_LEASE_DIR", "/var/lib/dotmac-deployment-control/leases"
)

_HOLDER: Final = "deployment-foundation-rehearsal"


def _parse_instant(value: str, *, field: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise SpecError(f"HostLease.{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError(
            f"HostLease.{field} {value!r} is not an ISO-8601 instant"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclasses.dataclass(frozen=True, slots=True)
class HostLease:
    """One holder's exclusive window on one target."""

    target: str
    holder: str
    authorization_run_id: str
    starts_at: str
    expires_at: str
    #: Every Compose project this lease may create must start with it, so the
    #: post-rehearsal deletion set is scoped by construction rather than by a
    #: human remembering which projects were theirs.
    compose_project_prefix: str
    controller_identity_fingerprint: str

    def __post_init__(self) -> None:
        for field in (
            "target",
            "holder",
            "authorization_run_id",
            "compose_project_prefix",
            "controller_identity_fingerprint",
        ):
            if not str(getattr(self, field)).strip():
                raise SpecError(
                    f"HostLease.{field} is empty. "
                    + (
                        "A lease is not self-granted: without the Platform CP "
                        "authorization run it references nothing, and a holder "
                        "writing its own lease has proved only that it can "
                        "write a file"
                        if field == "authorization_run_id"
                        else "Every field is something a reader checks"
                    )
                )
        if self.holder != _HOLDER:
            raise SpecError(
                f"HostLease.holder must be {_HOLDER!r}, got {self.holder!r}. "
                "The holder name is what another agent reads to know the host "
                "is taken, so it is a fixed token rather than free text"
            )
        start = _parse_instant(self.starts_at, field="starts_at")
        end = _parse_instant(self.expires_at, field="expires_at")
        if end <= start:
            raise SpecError(
                f"HostLease expires at {self.expires_at}, which is not after "
                f"{self.starts_at}. A lease with no duration is not a lease"
            )

    def covers(self, *, now: datetime, authorization_run_id: str) -> None:
        """Refuse unless this lease is live AND for this authorization run."""
        if self.authorization_run_id != str(authorization_run_id).strip():
            raise PreconditionFailed(
                f"the lease references authorization run "
                f"{self.authorization_run_id!r} and this execution is "
                f"{authorization_run_id!r}. A lease taken for one authorization "
                "does not cover another — that substitution is how a window "
                "granted for one piece of work comes to shelter a different one"
            )
        start = _parse_instant(self.starts_at, field="starts_at")
        end = _parse_instant(self.expires_at, field="expires_at")
        if now < start:
            raise PreconditionFailed(
                f"the lease on {self.target} does not begin until {self.starts_at}"
            )
        if now >= end:
            raise PreconditionFailed(
                f"the lease on {self.target} expired at {self.expires_at}. An "
                "expired lease is not a weak lease; the host may already have "
                "been handed to someone else"
            )

    def owns_project(self, project: str) -> bool:
        return str(project).startswith(self.compose_project_prefix)

    def as_document(self) -> dict[str, Any]:
        return {
            "schema": HOST_LEASE_SCHEMA,
            "target": self.target,
            "holder": self.holder,
            "authorization_run_id": self.authorization_run_id,
            "starts_at": self.starts_at,
            "expires_at": self.expires_at,
            "compose_project_prefix": self.compose_project_prefix,
            "controller_identity_fingerprint": self.controller_identity_fingerprint,
        }


def _lease_path(target: str, *, directory: str | Path = DEFAULT_LEASE_DIR) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in str(target))
    return Path(directory) / f"{safe}.json"


def write_lease(lease: HostLease, *, directory: str | Path = DEFAULT_LEASE_DIR) -> Path:
    """Persist a lease. Refuses to overwrite a live one held for another run."""
    path = _lease_path(lease.target, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = load_lease(lease.target, directory=directory)
        now = datetime.now(UTC)
        still_live = True
        try:
            existing.covers(now=now, authorization_run_id=existing.authorization_run_id)
        except PreconditionFailed:
            still_live = False
        if still_live and existing.authorization_run_id != lease.authorization_run_id:
            raise PreconditionFailed(
                f"{lease.target} is already leased to authorization run "
                f"{existing.authorization_run_id!r} until {existing.expires_at}. "
                "Taking it would make two holders believe they have exclusive "
                "use, which is worse than no lease at all because both would "
                "then skip the checks a shared host needs"
            )
    path.write_text(
        json.dumps(lease.as_document(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_lease(target: str, *, directory: str | Path = DEFAULT_LEASE_DIR) -> HostLease:
    path = _lease_path(target, directory=directory)
    if not path.exists():
        raise PreconditionFailed(
            f"no lease record for {target} at {path}. The rehearsal mutates a "
            "shared host and may not begin without one — and it cannot write "
            "its own, because a lease must reference a Platform CP "
            "authorization run"
        )
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SpecError(f"the lease at {path} is not valid JSON: {exc}") from exc
    if content.get("schema") != HOST_LEASE_SCHEMA:
        raise SpecError(
            f"expected {HOST_LEASE_SCHEMA} at {path}, got {content.get('schema')!r}"
        )
    return HostLease(
        target=str(content.get("target", "")),
        holder=str(content.get("holder", "")),
        authorization_run_id=str(content.get("authorization_run_id", "")),
        starts_at=str(content.get("starts_at", "")),
        expires_at=str(content.get("expires_at", "")),
        compose_project_prefix=str(content.get("compose_project_prefix", "")),
        controller_identity_fingerprint=str(
            content.get("controller_identity_fingerprint", "")
        ),
    )
