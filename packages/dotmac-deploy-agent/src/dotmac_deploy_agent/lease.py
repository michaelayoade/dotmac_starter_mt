"""Host lease client (DESIGN-rev3 s2.6.2): one writer per host.

The lease is a Control/CP record behind the target API, not a Foundation
`HostLease` and not a database handle. The agent takes it first and holds it for
the whole run; consumption is impossible without a live lease held by this run.
A crash leaves the lease to expire: it never leaves a second writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .client import Reply, TargetClient
from .refusal import Refusal

EXPIRED_UNRELEASED: Final = "EXPIRED_UNRELEASED"
MAX_TTL: Final = 3600


@dataclass(frozen=True)
class Lease:
    lease_id: str
    ttl_seconds: int


class LeaseClient:
    def __init__(self, client: TargetClient) -> None:
        self._client = client

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
        return Lease(lease_id, granted)

    def covers(self, lease: Lease, *, dispatch_id: str) -> bool:
        reply = self._post(
            "covers", {"lease_id": lease.lease_id, "dispatch_run": dispatch_id}
        )
        return reply.document.get("covers") is True

    def release(self, lease: Lease, *, host_mutated: bool) -> None:
        self._post(
            "release", {"lease_id": lease.lease_id, "host_mutated": host_mutated}
        )
