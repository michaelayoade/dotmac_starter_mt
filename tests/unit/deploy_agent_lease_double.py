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
* acquire by the SAME run returns its lease with its first ttl;
* renew grants `min(ttl, lease_max - held)`; signed refusals `lease_not_held`,
  `lease_expired`, `lease_max_reached` (expiry is checked before the cap);
* `covers` is a 200 `{covers: bool}` and never a refusal;
* a second release is a signed 200 replay `{released: true, replay: true}`.

Bodies, standing values and error codes are Control's. The vectors in
`target_api_v2.json` (`lease_body_vectors`, `lease_scenarios`,
`renew_grant_vectors`) are replayed against this double by the lease tests.
Time is injected: `LeaseClock` is the one clock the agent's lease budget and this
store share.

Marker rule (open question): the v2 scenario "acquire without the reconcile marker
is a signed 409" has no prior expired lease, so the harness reads as "a marker is
always required". `marker_always_required` models that for the replay; by default
the marker is needed only after an expired, unreleased lease.
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
        self.marker = False
        self.marker_always_required = False
        self.rehearsal_live = False  # the other class holds the shared row
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

    # -- endpoints ---------------------------------------------------------
    def handlers(self) -> dict[str, Callable[[dict[str, object]], dict[str, object]]]:
        return {
            "lease/standing": lambda _p: {"standing": self.standing()},
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
            # The same run: its own lease, with its FIRST ttl (not what is left).
            return {"lease_id": row.lease_id, "ttl_seconds": row.ttl}
        expired_unreleased = row is not None and not row.released
        if (expired_unreleased or self.marker_always_required) and not self.marker:
            raise LeaseRefused(409, MARKER_REQUIRED)
        self.counter += 1
        self.marker = False
        total = ttl if self.max_seconds is None else self.max_seconds
        number = f"{self.counter:04d}" if self.marker_always_required else self.counter
        self.row = _Row(
            f"lease-{number}", run, ttl, total, self.clock.t, self.clock.t + ttl
        )
        return {"lease_id": self.row.lease_id, "ttl_seconds": ttl}

    def covers(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/covers", dict(payload)))
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
        row = self.row
        if (
            row is None
            or row.released
            or payload.get("lease_id") != row.lease_id
            or payload.get("dispatch_run") != row.run
        ):
            raise LeaseRefused(409, "lease_not_held")
        if self.clock.t >= row.expires_at:
            raise LeaseRefused(409, "lease_expired")
        grant = renew_grant(row.ttl, row.max_seconds, self.clock.t - row.acquired_at)
        row.expires_at = self.clock.t + grant
        return {"lease_id": row.lease_id, "ttl_seconds": grant}

    def release(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/release", dict(payload)))
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
