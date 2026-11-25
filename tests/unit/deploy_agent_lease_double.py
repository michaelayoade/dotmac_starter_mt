"""A stateful double of the CP lease endpoints, following Control's
`target_api_v2.json` (lease section; CP lane 5c builds the real one).

It mirrors the production lease CLASS next to the rehearsal class:

* one `host_current_lease` row per host, SHARED with the rehearsal class (a live
  rehearsal lease refuses a deploy acquire with `lease_held`);
* a RELEASED lease is the clean state whatever `host_mutated` says;
* a lease that expired unreleased reports standing `expired_unreleased`, and the
  next acquire is refused with `reconcile_marker_required` until a marker exists;
  v2 has NO "reconciled" standing, so the standing stays `expired_unreleased`
  until a new acquire succeeds;
* acquire by the SAME run returns its lease with its REMAINING ttl;
* renew grants `min(ttl, lease_max - held)`; signed refusals `lease_not_held`,
  `lease_expired`, `lease_max_reached` (expiry is checked before the cap);
* `covers` is a 200 `{covers: bool}`; only a superseded lease is a signed 409;
* a second release is a signed 200 replay `{released: true, replay: true}`;
* releasing an EXPIRED lease is a signed 409 `lease_expired` and the host stays
  `expired_unreleased`; covers/renew/release of a lease that a newer acquire
  superseded are 409 `lease_superseded`.

Bodies, standing values and error codes are Control's. The vectors in
`target_api_v2.json` (`lease_body_vectors`, `lease_scenarios`,
`renew_grant_vectors`) are replayed against this double by the lease tests.
Time is injected: `LeaseClock` is the one clock the agent's lease budget and this
store share.

Marker rule (settled in v2): a marker is required only when the target's latest
lease row is expired_unreleased; a never-leased or cleanly released host acquires
without one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from dotmac_deploy_agent import wire
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.lease import LeaseClient

MARKER_REQUIRED = "reconcile_marker_required"
HELD = "lease_held"


class LeaseClock:
    """Seconds on one shared monotonic scale."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class LeaseRefused(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class _Row:
    lease_id: str
    run: str
    ttl: int
    max_seconds: int
    acquired_at: float
    expires_at: float
    released: bool = False
    mutated: bool | None = None
    #: A run_job lease is granted once and never renewed (`lease_not_renewable`).
    renewable: bool = True


def renew_grant(ttl: int, lease_max: int, held: float) -> int:
    """`min(ttl, lease_max - held)`; nothing is granted at or past the maximum."""
    if held >= lease_max:
        raise LeaseRefused(409, "lease_max_reached")
    return min(ttl, int(lease_max - held))


class DeployLeaseDouble:
    def __init__(self, clock: LeaseClock, *, max_seconds: int | None = None):
        self.clock = clock
        #: The plan's `lease_max_seconds`; None means "one grant" (= its ttl).
        self.max_seconds = max_seconds
        self.row: _Row | None = None
        #: Every lease ever granted, by id: a lease that is not the CURRENT one is
        #: `lease_superseded` (a newer acquire took the host), an unknown id is
        #: `lease_not_held`.
        self.history: dict[str, _Row] = {}
        self.marker = False
        self.rehearsal_live = False  # the other class holds the shared row
        #: Control's replay scenario ids are zero-padded (`lease-0001`).
        self.padded_ids = False
        #: Set by the plan (a serving plan renews; a run_job plan does not).
        self.renewable = True
        self.counter = 0
        self.log: list[tuple[str, dict[str, object]]] = []

    # -- state -------------------------------------------------------------
    def _live(self) -> bool:
        row = self.row
        return row is not None and not row.released and self.clock.t < row.expires_at

    def standing(self) -> str:
        if self.rehearsal_live or self._live():
            return "held"
        row = self.row
        if row is not None and not row.released:
            return "expired_unreleased"
        return "free"

    def _standing_body(self, _payload: dict[str, object]) -> dict[str, object]:
        """`{standing, dispatch_run}`; the dispatch is named only for an expired,
        unreleased lease and is null otherwise."""
        expired = self.standing() == "expired_unreleased" and self.row is not None
        return {
            "standing": self.standing(),
            "dispatch_run": self.row.run if expired and self.row else None,
        }

    # -- endpoints ---------------------------------------------------------
    def handlers(self) -> dict[str, Callable[[dict[str, object]], dict[str, object]]]:
        return {
            "lease/standing": self._standing_body,
            "lease/acquire": self.acquire,
            "lease/covers": self.covers,
            "lease/renew": self.renew,
            "lease/release": self.release,
        }

    def acquire(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/acquire", dict(payload)))
        run, ttl = payload.get("dispatch_run"), payload.get("ttl_seconds")
        if not isinstance(run, str) or not run:
            raise LeaseRefused(400, "body_value")
        if type(ttl) is not int or ttl < 1:
            raise LeaseRefused(400, "body_value")
        if self.rehearsal_live:
            raise LeaseRefused(409, HELD)
        row = self.row
        if row is not None and self._live():
            if row.run != run:
                raise LeaseRefused(409, HELD)
            # The same run: its own lease, with what is LEFT of it (v2 scenario).
            left = max(1, int(row.expires_at - self.clock.t))
            return {"lease_id": row.lease_id, "ttl_seconds": left}
        # A marker is needed ONLY when the latest lease row expired unreleased: a
        # never-leased host and a cleanly released one acquire without it.
        expired_unreleased = row is not None and not row.released
        if expired_unreleased and not self.marker:
            raise LeaseRefused(409, MARKER_REQUIRED)
        self.counter += 1
        self.marker = False
        total = ttl if self.max_seconds is None else self.max_seconds
        number = f"{self.counter:04d}" if self.padded_ids else self.counter
        self.row = _Row(
            f"lease-{number}", run, ttl, total, self.clock.t, self.clock.t + ttl
        )
        self.row.renewable = self.renewable
        self.history[self.row.lease_id] = self.row
        return {"lease_id": self.row.lease_id, "ttl_seconds": ttl}

    def _superseded(self, payload: dict[str, object]) -> None:
        row = self.history.get(str(payload.get("lease_id")))
        if row is not None and self.row is not None and row is not self.row:
            raise LeaseRefused(409, "lease_superseded")

    def covers(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/covers", dict(payload)))
        self._superseded(payload)
        row = self.row
        held = (
            row is not None
            and payload.get("lease_id") == row.lease_id
            and payload.get("dispatch_run") == row.run
            and self._live()
        )
        return {"covers": bool(held)}

    def renew(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/renew", dict(payload)))
        self._superseded(payload)
        row = self.row
        if (
            row is None
            or row.released
            or payload.get("lease_id") != row.lease_id
            or payload.get("dispatch_run") != row.run
        ):
            raise LeaseRefused(409, "lease_not_held")
        if not row.renewable:
            raise LeaseRefused(409, "lease_not_renewable")
        if self.clock.t >= row.expires_at:
            raise LeaseRefused(409, "lease_expired")
        grant = renew_grant(row.ttl, row.max_seconds, self.clock.t - row.acquired_at)
        row.expires_at = self.clock.t + grant
        return {"lease_id": row.lease_id, "ttl_seconds": grant}

    def release(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/release", dict(payload)))
        self._superseded(payload)
        row = self.row
        if row is None or payload.get("lease_id") != row.lease_id:
            raise LeaseRefused(409, "lease_not_held")
        if row.released:
            return {"released": True, "replay": True}  # signed 200, never a 4xx
        if self.clock.t >= row.expires_at:
            raise LeaseRefused(409, "lease_expired")  # stays unreleased
        row.released, row.mutated = True, bool(payload["host_mutated"])
        return {"released": True, "replay": False}

    # -- reconcile (Control side) -----------------------------------------
    def reconcile(self, observation: Mapping[str, object], *, expected: str) -> bool:
        """A marker is issued ONLY when every container runs the expected release
        digest. Anything else is the restore-and-fix-forward path (no marker)."""
        details = observation.get("details")
        containers = details.get("containers") if isinstance(details, dict) else None
        if observation.get("kind") != "reconcile" or not isinstance(containers, list):
            return False
        if containers and all(
            isinstance(c, dict)
            and any(
                isinstance(d, str) and d.endswith("@" + expected)
                for d in c.get("repo_digests", [])
            )
            for c in containers
        ):
            self.marker = True
        return self.marker


def install(rig: object, double: DeployLeaseDouble, clock: LeaseClock) -> None:
    """Route the rig's lease endpoints to `double` and give the launcher a lease
    client on the shared clock, with no keeper thread (tests call `tick`).
    Refusals come back as signed 4xx bodies."""
    cp = rig.cp  # type: ignore[attr-defined]
    launcher = rig.launcher  # type: ignore[attr-defined]
    cp.handlers = double.handlers()
    original = cp.request

    def request(method: str, path: str, headers: Mapping[str, str], body: bytes):  # type: ignore[no-untyped-def]
        try:
            return original(method, path, headers, body)
        except LeaseRefused as refused:
            return cp._reply(
                method, path, headers[wire.H_NONCE], refused.status,
                {"error": refused.code},
            )  # fmt: skip

    cp.request = request
    client: TargetClient = launcher.client
    launcher.lease = LeaseClient(client, monotonic=clock.monotonic, background=False)
