"""Host lease client (DESIGN-rev3 s2.6.2): one writer per host.

The lease is a Control/CP record behind the target API, not a Foundation
`HostLease` and not a database handle. The agent takes it first and holds it for
the whole run; consumption is impossible without a live lease held by this run.
A crash leaves the lease to expire: it never leaves a second writer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from .client import Reply, TargetClient
from .refusal import Refusal

EXPIRED_UNRELEASED: Final = "EXPIRED_UNRELEASED"
MAX_TTL: Final = 3600
#: Kept back from the lease for the runner's TERM-to-KILL wait (60 s) and the
#: release call; the same 120 s a job plan adds to its timeout (lease fit).
RUN_MARGIN: Final = 120
#: A run given less than this is not started: the lease is spent already.
MIN_RUN_SECONDS: Final = 60
_ALREADY_RELEASED: Final = "lease-already-released"


@dataclass(frozen=True)
class Lease:
    lease_id: str
    ttl_seconds: int
    #: Local monotonic time at which this lease is over. Measured from BEFORE the
    #: acquire request left, so it never outlives CP's own clock for the lease.
    #: The default (0.0) reads as already over: a lease built without one fails
    #: closed.
    deadline: float = 0.0


class LeaseClient:
    def __init__(
        self,
        client: TargetClient,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._monotonic = monotonic

    @staticmethod
    def _refuse_as_lease(refusal: Refusal) -> Refusal:
        if refusal.code == "cp_refused":
            return Refusal("lease_refused", refusal.detail)
        return refusal

    def _post(self, endpoint: str, payload: dict[str, object]) -> Reply:
        try:
            return self._client.call("POST", f"lease/{endpoint}", payload)
        except Refusal as refusal:
            raise self._refuse_as_lease(refusal) from None

    def standing(self) -> str:
        try:
            reply = self._client.call("GET", "lease/standing", None)
        except Refusal as refusal:
            raise self._refuse_as_lease(refusal) from None
        value = reply.document.get("standing")
        if not isinstance(value, str) or not value or len(value) > 64:
            raise Refusal("response_malformed")
        return value

    def acquire(self, *, dispatch_id: str, ttl_seconds: int) -> Lease:
        if not 1 <= ttl_seconds <= MAX_TTL:
            raise Refusal("lease_ttl_invalid")
        sent = self._monotonic()
        reply = self._post(
            "acquire", {"dispatch_run": dispatch_id, "ttl_seconds": ttl_seconds}
        )
        lease_id = reply.document.get("lease_id")
        granted = reply.document.get("ttl_seconds")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 128:
            raise Refusal("lease_refused")
        # The plan bound this TTL; CP may not grant a longer one.
        if type(granted) is not int or not 0 < granted <= ttl_seconds:
            # CP holds a lease the plan did not bind: give it straight back.
            try:
                self.release(Lease(lease_id, ttl_seconds), host_mutated=False)
            except Refusal:
                pass
            raise Refusal("lease_ttl_invalid")
        return Lease(lease_id, granted, sent + granted)

    def covers(self, lease: Lease, *, dispatch_id: str) -> bool:
        reply = self._post(
            "covers", {"lease_id": lease.lease_id, "dispatch_run": dispatch_id}
        )
        return reply.document.get("covers") is True

    def remaining(self, lease: Lease) -> float:
        return lease.deadline - self._monotonic()

    def run_budget(self, lease: Lease, *, configured_seconds: int) -> int:
        """Seconds the Foundation run may take so that it is over, killed if need
        be, BEFORE the lease ends. There is no renew (s2.6.2: acquire, covers,
        release, standing): a run outliving its lease would leave the host to a
        second writer, so the run is clipped to the lease, never the lease to the
        run. Too little left refuses the run."""
        budget = int(self.remaining(lease)) - RUN_MARGIN
        if budget < MIN_RUN_SECONDS:
            raise Refusal("lease_exhausted")
        return min(configured_seconds, budget)

    def release(self, lease: Lease, *, host_mutated: bool) -> None:
        try:
            self._post(
                "release", {"lease_id": lease.lease_id, "host_mutated": host_mutated}
            )
        except Refusal as refusal:
            # The contract calls a second release a replay, not an error.
            if refusal.detail == _ALREADY_RELEASED:
                return
            raise
