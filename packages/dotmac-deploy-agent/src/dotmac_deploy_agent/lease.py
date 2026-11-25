"""Host lease client (DESIGN-rev3 s2.6.2): one writer per host.

The lease is a Control/CP record behind the target API, not a Foundation
`HostLease` and not a database handle. The agent takes it first and holds it for
the whole run; consumption is impossible without a live lease held by this run.
A crash leaves the lease to expire: it never leaves a second writer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, cast

from .client import Reply, TargetClient
from .refusal import Refusal
from .transport import TIMEOUT_SECONDS

#: Control `target_api_v2.json` `standing_values`: the ONLY standings CP may report.
STANDING_VALUES: Final = ("free", "held", "expired_unreleased")
EXPIRED_UNRELEASED: Final = "expired_unreleased"
#: A string or null, always present (Control's "optstr").
OPTSTR: Final = "optstr"
#: `lease_bodies` of the same file: exact key sets and strict types per route.
_BODIES: Final[dict[str, dict[str, dict[str, object]]]] = {
    "lease/acquire": {
        "request": {"dispatch_run": str, "ttl_seconds": int},
        "response": {"lease_id": str, "ttl_seconds": int},
    },
    "lease/covers": {
        "request": {"dispatch_run": str, "lease_id": str},
        "response": {"covers": bool},
    },
    "lease/release": {
        "request": {"host_mutated": bool, "lease_id": str},
        "response": {"released": bool, "replay": bool},
    },
    "lease/renew": {
        "request": {"dispatch_run": str, "lease_id": str},
        "response": {"lease_id": str, "ttl_seconds": int},
    },
    # `dispatch_run`: the dispatch of an expired, unreleased lease row, else null.
    "lease/standing": {
        "request": {},
        "response": {"dispatch_run": OPTSTR, "standing": str},
    },
}
MAX_TTL: Final = 3600
#: Architect ruling 4 / Control `lease_ttl_range`: a SERVING plan's per-grant ttl.
SERVING_TTL: Final = (120, 900)
#: The total a serving plan may hold across renewals (Control `lease_max_limit`).
MAX_HOLD: Final = 10800
#: Kept back from the lease for the runner's TERM-to-KILL wait (60 s) and the
#: release call; the same 120 s a job plan adds to its timeout (lease fit).
RUN_MARGIN: Final = 120
#: A run given less than this is not started: the lease is spent already.
MIN_RUN_SECONDS: Final = 60
#: Signed renew refusals (Control `_v2`): the lease is gone or at its total cap.
RENEW_REFUSALS: Final = frozenset(
    {
        "lease_not_held",
        "lease_expired",
        "lease_max_reached",
        "lease_not_renewable",
        "lease_superseded",  # a newer lease took the host: ours is lost
    }
)
SUPERSEDED: Final = "lease_superseded"
#: How often the keeper thread looks at the clock; a renew is due every ttl/3.
POLL_SECONDS: Final = 2.0
#: A failed, non-lost renew waits `min(RETRY_SECONDS, ttl/10)` before the next try.
RETRY_SECONDS: Final = 15.0
#: The keeper thread is joined for at least three transport timeouts.
JOIN_SECONDS: Final = 3 * TIMEOUT_SECONDS


def check_body(route: str, direction: str, body: object) -> None:
    """The v2 lease body rules, shared by what the agent sends and what it accepts.
    Raises `Refusal("response_malformed", <code>)` with one of Control's codes:
    `unknown_body`, `body_keys`, `body_type`, `body_value`."""
    shape = _BODIES.get(route, {}).get(direction)
    if shape is None:
        raise Refusal("response_malformed", "unknown_body")
    if not isinstance(body, dict) or set(body) != set(shape):
        raise Refusal("response_malformed", "body_keys")
    for key, kind in shape.items():
        value = body[key]
        if kind == OPTSTR:
            if value is not None and type(value) is not str:
                raise Refusal("response_malformed", "body_type")
            if value == "":
                raise Refusal("response_malformed", "body_value")
            continue
        if type(value) is not kind:  # a bool is never an int
            raise Refusal("response_malformed", "body_type")
        if kind is str and not value:
            raise Refusal("response_malformed", "body_value")
    if "ttl_seconds" in shape and cast("int", body["ttl_seconds"]) <= 0:
        raise Refusal("response_malformed", "body_value")
    if route == "lease/standing" and direction == "response":
        standing, run = body["standing"], body["dispatch_run"]
        if standing not in STANDING_VALUES:
            raise Refusal("response_malformed", "body_value")
        # An expired, unreleased row names its dispatch; every other standing is null.
        if (standing == EXPIRED_UNRELEASED) != (run is not None):
            raise Refusal("response_malformed", "body_value")


def _answer(reply: Reply, route: str) -> dict[str, object]:
    document = {k: v for k, v in reply.document.items() if k != "store_now"}
    check_body(route, "response", document)
    return document


@dataclass(frozen=True)
class Lease:
    lease_id: str
    ttl_seconds: int
    #: Local monotonic time at which the CURRENT grant is over. Measured from
    #: BEFORE the request left, so it never outlives CP's own clock. The default
    #: (0.0) reads as already over: a lease built without one fails closed.
    deadline: float = 0.0
    #: Local monotonic time the lease was first requested, and the most it may be
    #: held in total (`lease_max_seconds`). The run budget comes from these, not
    #: from the current grant, because renewal keeps the grant alive.
    started: float = 0.0
    max_seconds: int = 0
    #: The per-grant ttl the plan asked for: the most any grant may be. The
    #: current `ttl_seconds` can be shorter (a replayed acquire returns what is left).
    cap: int = 0


class LeaseKeeper:
    """Renews the lease every ttl/3 through transfer and the Foundation run.

    `tick` is the whole logic (the thread only calls it), so it is testable with
    an injected clock. A refused renewal stops renewing and records `lease_lost`;
    it NEVER kills or signals Foundation: the run ends inside its own budget, and
    an expired lease already forces EXPIRED_UNRELEASED, then a reconcile, before
    the next acquire.
    """

    def __init__(self, client: LeaseClient, lease: Lease, *, dispatch_id: str) -> None:
        self._client = client
        self._dispatch_id = dispatch_id
        self._lease = lease
        self._last_ok = lease.deadline - lease.ttl_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: A failed, non-lost renew is not retried before this (monotonic) time.
        self._retry_at = 0.0
        #: False when the thread was still alive after the join (R8-L1).
        self.joined = True
        self.renewals = 0
        self.lease_lost: str | None = None

    @property
    def lease(self) -> Lease:
        return self._lease

    def tick(self) -> None:
        if self.lease_lost is not None:
            return
        now = self._client.monotonic()
        lease = self._lease
        if now >= lease.deadline:
            self.lease_lost = "lease_expired_locally"  # nothing renewed it in time
            return
        if now >= lease.started + lease.max_seconds:
            return  # the plan's total is spent: nothing left to renew into
        if now < self._last_ok + lease.ttl_seconds / 3 or now < self._retry_at:
            return
        if self._stop.is_set():
            return  # R8-L1: stop() was called: no renew may race the release
        try:
            renewed = self._client.renew(lease, dispatch_id=self._dispatch_id)
        except Refusal as refusal:
            if refusal.detail in RENEW_REFUSALS:
                self.lease_lost = refusal.detail
            # Anything else (unreachable, 5xx) is retried after a pause, not every
            # poll (R8-L2); the deadline check above turns a long outage into
            # `lease_lost`.
            self._retry_at = now + min(RETRY_SECONDS, lease.ttl_seconds / 10)
            return
        self._lease = renewed
        self._last_ok = renewed.deadline - renewed.ttl_seconds  # cadence follows grants
        self.renewals += 1

    def start(self) -> None:
        if not self._client.background or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="dotmac-lease-keeper", daemon=True
        )
        self._thread.start()

    def run_tick(self) -> None:
        try:
            self.tick()
        except Exception:  # a keeper must never raise into the run
            self.lease_lost = self.lease_lost or "lease_keeper_failed"

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_tick()
            self._stop.wait(self._client.poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # At least three transport timeouts: one in-flight renew call must be
            # able to finish before the release goes out.
            self._thread.join(timeout=JOIN_SECONDS)
            self.joined = not self._thread.is_alive()
            self._thread = None

    def as_details(self) -> dict[str, object]:
        details: dict[str, object] = {"renewals": self.renewals}
        if not self.joined:
            details["keeper_joined"] = False
        if self.lease_lost is not None:
            details["lease_lost"] = self.lease_lost
        return details


class LeaseClient:
    def __init__(
        self,
        client: TargetClient,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        background: bool = True,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._client = client
        self._monotonic = monotonic
        self.background = background
        self.poll_seconds = poll_seconds
        self.last_keeper: LeaseKeeper | None = None

    def monotonic(self) -> float:
        return self._monotonic()

    @staticmethod
    def _refuse_as_lease(refusal: Refusal) -> Refusal:
        if refusal.code == "cp_refused":
            return Refusal("lease_refused", refusal.detail)
        return refusal

    def _post(self, endpoint: str, payload: dict[str, object]) -> Reply:
        check_body(
            f"lease/{endpoint}", "request", payload
        )  # what we send is closed too
        try:
            return self._client.call("POST", f"lease/{endpoint}", payload)
        except Refusal as refusal:
            raise self._refuse_as_lease(refusal) from None

    def standing(self) -> str:
        try:
            reply = self._client.call("GET", "lease/standing", None)
        except Refusal as refusal:
            raise self._refuse_as_lease(refusal) from None
        return str(_answer(reply, "lease/standing")["standing"])

    def acquire(
        self,
        *,
        dispatch_id: str,
        ttl_seconds: int,
        max_seconds: int | None = None,
        serving: bool = True,
    ) -> Lease:
        """A serving plan: per-grant ttl 120..900, total `max_seconds` (ttl..10800,
        or absent = one grant). A run_job plan (`serving=False`): ttl = max =
        timeout + 120, at most 3600, one grant, never renewed."""
        if serving:
            low, high = SERVING_TTL
            ceiling = ttl_seconds if max_seconds is None else max_seconds
            if not low <= ttl_seconds <= high or not ttl_seconds <= ceiling <= MAX_HOLD:
                raise Refusal("lease_ttl_invalid")
        elif not 1 <= ttl_seconds <= MAX_TTL or max_seconds not in (None, ttl_seconds):
            raise Refusal("lease_ttl_invalid")
        sent = self._monotonic()
        reply = self._post(
            "acquire", {"dispatch_run": dispatch_id, "ttl_seconds": ttl_seconds}
        )
        answer = _answer(reply, "lease/acquire")
        lease_id = cast("str", answer["lease_id"])
        granted = cast("int", answer["ttl_seconds"])
        if len(lease_id) > 128:
            raise Refusal("lease_refused")
        # The plan bound this TTL; CP may not grant a longer one.
        if not 0 < granted <= ttl_seconds:
            # CP holds a lease the plan did not bind: give it straight back.
            try:
                self.release(Lease(lease_id, ttl_seconds), host_mutated=False)
            except Refusal:
                pass
            raise Refusal("lease_ttl_invalid")
        # No `lease_max_seconds` means one grant is all the plan allows (job plans).
        total = granted if max_seconds is None else max_seconds
        if granted < ttl_seconds:
            # Shorter than asked: possibly a replayed acquire, whose lease began
            # earlier by an amount we cannot see. Never count on more than we were
            # given: the whole hold is what this grant covers, measured from receipt.
            total = min(total, granted)
        # Deadline from the ttl RETURNED, counted from before the request left.
        return Lease(lease_id, granted, sent + granted, sent, total, ttl_seconds)

    def covers(self, lease: Lease, *, dispatch_id: str) -> bool:
        try:
            reply = self._post(
                "covers", {"lease_id": lease.lease_id, "dispatch_run": dispatch_id}
            )
        except Refusal as refusal:
            if refusal.detail == SUPERSEDED:
                return False  # a newer lease holds the host: this one covers nothing
            raise
        return _answer(reply, "lease/covers")["covers"] is True

    def renew(self, lease: Lease, *, dispatch_id: str) -> Lease:
        """`POST lease/renew {lease_id, dispatch_run}` -> `{lease_id, ttl_seconds}`.
        A grant is never longer than the ttl the plan asked for (`cap`)."""
        sent = self._monotonic()
        reply = self._post(
            "renew", {"lease_id": lease.lease_id, "dispatch_run": dispatch_id}
        )
        answer = _answer(reply, "lease/renew")
        granted = cast("int", answer["ttl_seconds"])
        # A grant is never longer than the ttl the plan asked for (`cap`); the
        # current ttl may be shorter (a replayed acquire returns what is left).
        if answer["lease_id"] != lease.lease_id or granted > (
            lease.cap or lease.ttl_seconds
        ):
            raise Refusal("lease_refused", "renew_malformed")
        return Lease(
            lease.lease_id,
            granted,
            sent + granted,
            lease.started,
            lease.max_seconds,
            lease.cap,
        )

    def keeper(self, lease: Lease, *, dispatch_id: str) -> LeaseKeeper:
        self.last_keeper = LeaseKeeper(self, lease, dispatch_id=dispatch_id)
        return self.last_keeper

    def remaining(self, lease: Lease) -> float:
        """What is left of the plan's TOTAL, not of the current grant."""
        return lease.started + lease.max_seconds - self._monotonic()

    def run_budget(self, lease: Lease, *, configured_seconds: int) -> int:
        """`min(timeout, lease_max - elapsed - 120)`. Renewal keeps the lease alive
        for the whole budget, so the run is never cut short by a grant ending; the
        margin covers the runner's TERM-to-KILL wait and the release. Too little
        left refuses the run."""
        budget = int(self.remaining(lease)) - RUN_MARGIN
        if budget < MIN_RUN_SECONDS:
            raise Refusal("lease_exhausted")
        return min(configured_seconds, budget)

    def release(self, lease: Lease, *, host_mutated: bool) -> None:
        reply = self._post(
            "release", {"lease_id": lease.lease_id, "host_mutated": host_mutated}
        )
        # A second release is a signed 200 replay `{released, replay}`; any 4xx
        # already raised above as a refusal.
        if _answer(reply, "lease/release")["released"] is not True:
            raise Refusal("lease_refused", "release_malformed")
