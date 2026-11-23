"""A stateful double of the CP lease endpoints (DESIGN-rev3 s2.6.2, CP lane 5c).

It mirrors the production lease CLASS next to the rehearsal class:

* one `host_current_lease` row per host, SHARED with the rehearsal class (a live
  rehearsal lease refuses a deploy acquire with `lease-held-by-another-run`);
* append-only releases: a RELEASED lease is the clean state whatever
  `host_mutated` says (the persistent-host rule);
* a lease that expired unreleased sets standing `EXPIRED_UNRELEASED`, and the
  next acquire is refused until a reconcile marker exists; the marker is issued
  only when the observed release equals the expected one;
* acquire by the SAME run returns its lease; a second release is a replay
  (contract `retry_idempotency`), or `lease-already-released` when `strict`;
* closed refusal codes come from the #266 store (`REFUSAL_CODES`).

Provisional, because s2.6.2 and the vendored contract do not pin them: the
request/response BODY keys (taken from the agent client), the standing values
other than `EXPIRED_UNRELEASED`, and the marker refusal code. Time is injected:
`LeaseClock` is the one clock both the agent's lease budget and this store use.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from dotmac_deploy_agent import wire
from dotmac_deploy_agent.client import TargetClient
from dotmac_deploy_agent.lease import LeaseClient

MAX_TTL = 3600
MARKER_REQUIRED = "reconcile-marker-required"  # provisional name


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
    expires_at: float
    released: bool = False
    mutated: bool | None = None


class DeployLeaseDouble:
    def __init__(self, clock: LeaseClock, *, strict_double_release: bool = False):
        self.clock = clock
        self.strict = strict_double_release
        self.row: _Row | None = None
        self.marker = False
        self.rehearsal_live = False  # the other class holds the shared row
        self.counter = 0
        self.log: list[tuple[str, dict[str, object]]] = []

    # -- state -------------------------------------------------------------
    def _live(self) -> bool:
        row = self.row
        return row is not None and not row.released and self.clock.t < row.expires_at

    def standing(self) -> str:
        if self.rehearsal_live or self._live():
            return "HELD"
        row = self.row
        if row is not None and not row.released:
            return "RECONCILED" if self.marker else "EXPIRED_UNRELEASED"
        return "CLEAN"

    # -- endpoints ---------------------------------------------------------
    def handlers(self) -> dict[str, Callable[[dict[str, object]], dict[str, object]]]:
        return {
            "lease/standing": lambda _p: {"standing": self.standing()},
            "lease/acquire": self.acquire,
            "lease/covers": self.covers,
            "lease/release": self.release,
        }

    def acquire(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/acquire", dict(payload)))
        run, ttl = payload.get("dispatch_run"), payload.get("ttl_seconds")
        if not isinstance(run, str) or not run:
            raise LeaseRefused(400, "lease-document-mismatch")
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
            raise LeaseRefused(400, "lease-ttl-invalid")
        if self.rehearsal_live:
            raise LeaseRefused(409, "lease-held-by-another-run")
        row = self.row
        if row is not None and self._live():
            if row.run != run:
                raise LeaseRefused(409, "lease-held-by-another-run")
            return {  # the same run: its own lease, with what is left of it
                "lease_id": row.lease_id,
                "ttl_seconds": max(1, int(row.expires_at - self.clock.t)),
            }
        if row is not None and not row.released and not self.marker:
            raise LeaseRefused(409, MARKER_REQUIRED)
        self.counter += 1
        self.marker = False
        self.row = _Row(f"lease-{self.counter}", run, self.clock.t + ttl)
        return {"lease_id": self.row.lease_id, "ttl_seconds": ttl}

    def covers(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/covers", dict(payload)))
        row = self._held(payload)
        if row.released or self.clock.t >= row.expires_at:
            raise LeaseRefused(409, "lease-expired")
        if row.run != payload.get("dispatch_run"):
            raise LeaseRefused(409, "lease-foreign-run")
        return {"covers": True}

    def release(self, payload: dict[str, object]) -> dict[str, object]:
        self.log.append(("lease/release", dict(payload)))
        row = self._held(payload)
        if not isinstance(payload.get("host_mutated"), bool):
            raise LeaseRefused(400, "host-mutated-required")
        if row.released:
            if self.strict:
                raise LeaseRefused(409, "lease-already-released")
            return {"released": True, "replay": True}
        if self.clock.t >= row.expires_at:
            raise LeaseRefused(409, "lease-expired")  # stays unreleased
        row.released, row.mutated = True, payload["host_mutated"]  # type: ignore[assignment]
        return {"released": True}

    def _held(self, payload: dict[str, object]) -> _Row:
        row = self.row
        if row is None or payload.get("lease_id") != row.lease_id:
            raise LeaseRefused(404, "release-not-this-lease")
        return row

    # -- reconcile (Control side) -----------------------------------------
    def reconcile(self, observation: Mapping[str, object], *, expected: str) -> bool:
        """A marker is issued ONLY when a container runs the expected release
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
    client on the shared clock. Refusals come back as signed 4xx bodies."""
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
    launcher.lease = LeaseClient(client, monotonic=clock.monotonic)
