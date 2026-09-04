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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .errors import PreconditionFailed, SpecError

__all__ = [
    "HOST_LEASE_SCHEMA",
    "DEFAULT_LEASE_DIR",
    "HistoricalLeaseV1",
    "HostLease",
    "HOST_LEASE_SCHEMA_V1",
    "LEASE_HISTORICAL",
    "load_lease",
    "release_path",
    "write_lease",
]

#: The schema this version WRITES. V2, because V1's reader and writer shipped in
#: five built candidate wheels — `0.3.0a1` through `0.3.0a5`, including
#: `0.3.0a3`, which is Platform CP's bootstrap input. A contract that has crossed
#: an artifact boundary cannot be widened: a wheel already in circulation would
#: write documents the new reader refuses, under the same schema name. That is
#: `0.3.0a2`'s one-name-two-contracts defect, chosen deliberately.
HOST_LEASE_SCHEMA: Final = "HostLease.v2"

#: The shipped predecessor. READABLE AS HISTORY and unable to authorize anything
#: — see `HistoricalLeaseV1`, which deliberately has no `covers()` and no
#: workload principal. A V1 lease does not acquire a principal by being read.
HOST_LEASE_SCHEMA_V1: Final = "HostLease.v1"

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
    #: The credential USED TO MUTATE THE HOST. A separate FACT from who holds
    #: the lease — and not a separate DOCUMENT: it is inside the canonical bytes,
    #: so a lease that swapped credentials while keeping its principal digests
    #: differently.
    controller_identity_fingerprint: str
    #: The authenticated runner/controller that HOLDS and RELEASES this lease.
    #:
    #: Distinct from `holder`, which is the authorized ROLE and is a fixed token.
    #: Three fields, three facts: what role was authorized, who held it, and what
    #: credential touched the machine. `released_by` must equal THIS value, so a
    #: changed workload principal requires a newly issued lease rather than a
    #: quietly re-used one.
    workload_principal: str

    def __post_init__(self) -> None:
        for field in (
            "target",
            "holder",
            "authorization_run_id",
            "compose_project_prefix",
            "controller_identity_fingerprint",
            "workload_principal",
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
            "workload_principal": self.workload_principal,
        }


#: Refused: a V1 lease was offered where authority is required.
LEASE_HISTORICAL: Final = "lease.historical"


@dataclasses.dataclass(frozen=True, slots=True)
class HistoricalLeaseV1:
    """A shipped V1 lease, readable and unable to authorize anything.

    It has no `covers()` and no workload principal, so a caller cannot use one to
    admit a run — not "it would be rejected", but there is no method to call and
    no principal to bind. That is the difference between a migration and a
    boundary: V1 records stay legible, and legibility is not authority.
    """

    target: str
    holder: str
    authorization_run_id: str
    starts_at: str
    expires_at: str
    compose_project_prefix: str
    controller_identity_fingerprint: str

    @classmethod
    def from_document(cls, document: Any) -> HistoricalLeaseV1:
        if document.get("schema") != HOST_LEASE_SCHEMA_V1:
            raise SpecError(
                f"this is not a {HOST_LEASE_SCHEMA_V1} record "
                f"(schema {document.get('schema')!r})",
                code=LEASE_HISTORICAL,
            )
        return cls(
            target=str(document.get("target", "")),
            holder=str(document.get("holder", "")),
            authorization_run_id=str(document.get("authorization_run_id", "")),
            starts_at=str(document.get("starts_at", "")),
            expires_at=str(document.get("expires_at", "")),
            compose_project_prefix=str(document.get("compose_project_prefix", "")),
            controller_identity_fingerprint=str(
                document.get("controller_identity_fingerprint", "")
            ),
        )


def _required_key(content: Any, key: str, path: Any) -> str:
    """Read a key that must be PRESENT, never defaulted.

    ``content.get(key, "")`` would turn an absent field into an empty one, and
    the empty one is then refused at construction — which looks like the same
    outcome and is not. A defaulted read means the DOCUMENT was accepted as
    carrying a value it does not carry, and every later reader sees a lease that
    claims an empty principal rather than a lease that names none.

    Same rule as the prestate discriminator: **a record does not acquire a fact
    by being read.** This is the second place it is enforced, and the first place
    it was violated — by the author who wrote the rule.
    """
    if key not in content:
        raise SpecError(
            f"the lease at {path} carries no {key!r}. A {HOST_LEASE_SCHEMA} "
            "record names its workload principal; a missing field is NOT an "
            "empty one, and defaulting it would let a document be read as "
            "carrying a value it does not carry",
            code=LEASE_HISTORICAL,
        )
    return str(content[key])


def release_path(target: str, *, directory: str | Path = DEFAULT_LEASE_DIR) -> Path:
    """Where the terminal release lives: BESIDE the lease, in the same store.

    Derived from `_lease_path` rather than composed independently, so the two
    records cannot come to live in different places. A release written anywhere
    else would be a SECOND LEDGER, and the destroy gate would then consult one
    record while the lease lived in another — which is exactly how a swapped
    lease goes unnoticed.

    Public because the destroyer needs it and `_lease_path` is private; that
    privacy was itself a finding, since a caller re-deriving the path would be a
    second opinion about where a lease lives.
    """
    lease = _lease_path(target, directory=directory)
    return lease.with_name(lease.name.removesuffix(".json") + ".release.json")


def _lease_path(target: str, *, directory: str | Path = DEFAULT_LEASE_DIR) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in str(target))
    return Path(directory) / f"{safe}.json"


def write_store_record(path: Path, document: Mapping[str, Any]) -> Path:
    """Turn a record of THIS store into bytes, and write it. One answer.

    Not a canonicalization for identity — that is `canonical_plan_bytes`, which
    both `lease_digest` and `HostLeaseReleaseV1.digest` already use with their
    own document schema. This is the STORE form: the shape a human reads in
    `.dotmac-leases/` and the parser round-trips. Two byte forms for two
    questions is correct, and neither one may have two implementations.

    It exists because it briefly did have two. `write_lease` and `write_release`
    each spelled `json.dumps(..., sort_keys=True, indent=2) + "\n"` inline. They
    agreed on the day they were written, which is the only day a duplicated
    literal ever agrees; a later change to indentation or separators in one would
    have left two records in ONE directory written two ways, with nothing to fail.
    The canonicalizing-population ratchet caught the second one appearing and
    asked which kind it was — the answer is that it is the same mechanism, so it
    is shared rather than registered.
    """
    path.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return path


def write_lease(
    lease: HostLease,
    *,
    directory: str | Path = DEFAULT_LEASE_DIR,
    now: datetime | None = None,
) -> Path:
    """Persist a lease. Refuses to overwrite a live one held for another run.

    ``now`` is injectable because liveness is the whole decision here: with the
    wall clock, a test asserting the refusal stops asserting anything the moment
    its fixture expiry passes, and it does so by going green-then-red on a date
    rather than on a code change. ``HostLease.covers`` already takes its clock;
    this was the one caller that did not.
    """
    path = _lease_path(lease.target, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = load_lease(lease.target, directory=directory)
        resolved_now = datetime.now(UTC) if now is None else now
        still_live = True
        try:
            existing.covers(
                now=resolved_now,
                authorization_run_id=existing.authorization_run_id,
            )
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
    return write_store_record(path, lease.as_document())


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
    found = content.get("schema")
    if found == HOST_LEASE_SCHEMA_V1:
        raise SpecError(
            f"the lease at {path} is a {HOST_LEASE_SCHEMA_V1} record. It is "
            "READABLE AS HISTORY through `HistoricalLeaseV1` and cannot "
            "authorize a new run or a target transfer: it names no workload "
            "principal, and one is NEVER inferred or defaulted. A record does "
            "not acquire a fact by being read. Issue a "
            f"{HOST_LEASE_SCHEMA} lease",
            code=LEASE_HISTORICAL,
        )
    if found != HOST_LEASE_SCHEMA:
        raise SpecError(f"expected {HOST_LEASE_SCHEMA} at {path}, got {found!r}")
    return HostLease(
        workload_principal=_required_key(content, "workload_principal", path),
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
