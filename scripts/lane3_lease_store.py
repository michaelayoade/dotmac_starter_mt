"""The lease-store interface the Lane 3 launcher requires (E7 / U5).

Michael's ruling (U5): the lease store is the CP production database, under the
per-run scoped role. This module defines ONLY the contract the launcher depends
on. It writes no table, no migration and no implementation: the CP side needs a
separate image-budget ruling, and nothing here touches the CP repository.

## The contract, in five rules

1. **Exclusive acquisition.** `acquire_exclusive` takes the host's lease row under
   a row lock. A second acquisition of a live lease for another authorization run
   refuses; two holders must never both believe they have exclusive use.
2. **Store time is the only clock.** `database_time` is the store's own wall
   clock. Expiry and `covers` are judged on it and on nothing else: never the
   target's clock and never the runner's. A store that cannot supply database
   time is refused by :func:`require_database_time`; the file-backed store of the
   controller-side runner is exactly such a store.
3. **Custody and release are one store.** The release record is written through
   `release`, into the same store that holds the lease. There is no second
   ledger, so the destroy gate reads one answer.
4. **A release says whether the host was mutated.** `release` takes
   `host_mutated` explicitly and records it. It cannot be defaulted.
5. **A dead runner never releases a host as untouched.** If a holder never
   releases, the lease EXPIRES on store time and `standing` answers
   `EXPIRED_MUTATED`. There is no transition from an expired lease to a clean
   release: `release` of an expired lease refuses.

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
    "HeldLease",
    "LeaseStanding",
    "LeaseStore",
    "StoreRefused",
    "require_database_time",
]

#: The only value `LeaseStore.time_authority` may carry.
DATABASE_TIME: Final = "database"


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
    ) -> HeldLease:
        """Take the host's lease under a row lock, or raise `StoreRefused`."""
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
