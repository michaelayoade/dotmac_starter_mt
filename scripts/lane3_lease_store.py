"""The lease-store interface the Lane 3 launcher requires (E7 / U5).

Michael's ruling (U5): the lease store is the CP production database, under the
per-run scoped role. This module defines ONLY the contract the launcher depends
on. It writes no table, no migration and no implementation: the CP side needs a
separate image-budget ruling, and nothing here touches the CP repository.

## The contract

1. **Exclusive acquisition.** `acquire_exclusive` takes the host's lease row under
   a row lock. Concurrent acquisitions of one host admit exactly one; a second
   acquisition of a live lease refuses.
2. **Store time is the only clock.** `database_time` is the store's own wall
   clock. Expiry, `covers` and `released_at` are judged and stamped on it and on
   nothing else: never the target's clock and never the runner's. A store that
   cannot supply database time is refused by :func:`require_database_time`; the
   file-backed store of the controller-side runner is exactly such a store.
3. **Custody and release are one store.** The release record is written through
   `release`, into the same store that holds the lease. There is no second
   ledger, so the destroy gate reads one answer.
4. **A release says whether the host was mutated.** `release` takes
   `host_mutated` explicitly and records it. It cannot be defaulted.
5. **A dead runner never releases a host as untouched.** If a holder never
   releases, the lease EXPIRES on store time and `standing` answers
   `EXPIRED_MUTATED`. `release` of an expired lease refuses.
6. **Mutation history is never wiped.** A host whose standing is
   `EXPIRED_MUTATED`, or that was released with `host_mutated=True`, is NEVER
   re-acquired. The only exception is an explicit :class:`ReprovisionMarker`
   naming the host and the provisioning receipt that replaced it. The marker is
   defined here; nothing but the reference fake implements it.
7. **One release.** A lease is released at most once; a second `release` refuses.
8. **A release must be this lease's.** `release` refuses unless the record's
   `lease_digest` is the digest of the held lease, its `released_by.subject` is
   the lease's `workload_principal`, and its `controller_identity_fingerprint` is
   the lease's.
9. **The lease document is the row.** The `HostLease` the store returns carries
   an `expires_at` equal to the row's `expires_at`; there is not one expiry in the
   document and another in the table.
10. **A capped TTL.** `ttl_seconds` must be positive and no more than
    :data:`MAX_TTL_SECONDS`; anything else refuses.
11. **`released_at` is store time.** The store stamps the release on its own clock
    (`released_at(host_id)`) and refuses a record whose own `released_at` is more
    than :data:`MAX_RELEASE_SKEW_SECONDS` from store time. The runner's clock
    never decides when a host was released.

## The target's role has no privileges on the lease tables

The target runs under a per-run SCOPED consumption role to consume the Control
dispatch. That role must hold NO privilege (not SELECT, not UPDATE, not a column
grant, not a lock) on any lease table. A compromised target must not be able to
read, extend or clear a lease; only the launcher's role writes this store.

The launcher enforces what it can from outside: it refuses a store that lacks
database time, judges `covers` on the store before it lets the target proceed,
passes the target the store's time (never its own), and forces
`host_mutated=True` once the target has reported `ready`.
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from dotmac_deployment_foundation.controller_identity import (
    ControllerSshFingerprintV1,
)
from dotmac_deployment_foundation.lease import HostLease
from dotmac_deployment_foundation.lease_release import HostLeaseReleaseV1

__all__ = [
    "DATABASE_TIME",
    "MAX_RELEASE_SKEW_SECONDS",
    "MAX_TTL_SECONDS",
    "HeldLease",
    "LeaseStanding",
    "LeaseStore",
    "ReprovisionMarker",
    "StoreRefused",
    "require_database_time",
]

#: The only value `LeaseStore.time_authority` may carry.
DATABASE_TIME: Final = "database"
#: The longest lease a store grants. A lease that outlives a run by hours is a
#: host nobody can take back.
MAX_TTL_SECONDS: Final = 3600
#: How far a release record's own `released_at` may sit from store time.
MAX_RELEASE_SKEW_SECONDS: Final = 60


@dataclasses.dataclass(frozen=True, slots=True)
class ReprovisionMarker:
    """The one explicit exception to "mutation history is never wiped".

    Names the host and the provisioning receipt (E10) that replaced the machine.
    Without it a mutated or expired host is never re-acquired.
    """

    host_id: str
    provisioning_receipt_ref: str


class StoreRefused(Exception):
    """The store, or the lease it holds, is unacceptable. A CODE, never a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LeaseStanding(enum.Enum):
    """What the store says about a host, on store time."""

    #: A live lease is held and no release exists.
    HELD = "held"
    #: A release record exists, written through `release`.
    RELEASED = "released"
    #: The lease expired without a release. The host is treated as MUTATED and is
    #: never read as released or reusable.
    EXPIRED_MUTATED = "expired_mutated"


@dataclasses.dataclass(frozen=True, slots=True)
class HeldLease:
    """A lease this launcher holds, with the store times it was judged on."""

    lease: HostLease
    #: Opaque to the launcher; names the row lock / row the store holds.
    handle: str
    #: Both are STORE time.
    acquired_at: datetime
    expires_at: datetime


@runtime_checkable
class LeaseStore(Protocol):
    """What the launcher needs from a lease store. See the module docstring."""

    #: Must equal :data:`DATABASE_TIME`.
    time_authority: str

    def database_time(self) -> datetime:
        """The store's own wall clock, timezone-aware."""
        ...

    def acquire_exclusive(
        self,
        *,
        host_id: str,
        authorization_run: str,
        controller_identity: ControllerSshFingerprintV1,
        ttl_seconds: int,
        reprovision: ReprovisionMarker | None = None,
    ) -> HeldLease:
        """Take the host's lease under a row lock, or raise `StoreRefused`.

        Refuses a non-positive or over-cap TTL, a live lease, and (without a
        `reprovision` marker for this host) a host that is expired-mutated or was
        released as mutated.
        """
        ...

    def covers(self, held: HeldLease, *, authorization_run: str) -> datetime:
        """Judge `held` on STORE time; return the instant judged, or refuse."""
        ...

    def release(
        self,
        held: HeldLease,
        *,
        release: HostLeaseReleaseV1,
        host_mutated: bool,
    ) -> str:
        """Record the release in this same store; return its digest.

        Refuses when `held` has expired on store time.
        """
        ...

    def standing(self, host_id: str) -> LeaseStanding:
        """The host's standing on store time."""
        ...

    def released_at(self, host_id: str) -> datetime | None:
        """When the store stamped the release, on store time; None if unreleased."""
        ...


def require_database_time(store: object) -> LeaseStore:
    """Return `store` only if it provably supplies database time, else refuse.

    Checked: it has the interface; it declares `time_authority == "database"`;
    and `database_time()` answers with a timezone-aware instant. A store that
    cannot supply database time cannot judge expiry, so it is not a lease store
    for this lane, however it persists records.
    """
    if not isinstance(store, LeaseStore):
        raise StoreRefused("store-lacks-interface")
    if getattr(store, "time_authority", None) != DATABASE_TIME:
        raise StoreRefused("store-lacks-database-time")
    try:
        instant = store.database_time()
    except Exception:
        raise StoreRefused("store-lacks-database-time") from None
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise StoreRefused("store-lacks-database-time")
    return store
