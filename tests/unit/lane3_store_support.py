"""An in-memory lease store, and the conformance cases a real one must pass.

E7 / U5. The CP production-database store does not exist yet (separate
image-budget ruling); this fake models the contract in
`scripts/lane3_lease_store.py` with an injected database clock, and
:data:`CASES` is the behaviour any implementation must show, one case per rule,
so the CP-side work can run each against its own store.

The lease and release are STAND-INS with the attribute names Foundation's real
types carry (`HostLease.target`, `workload_principal`, `expires_at`,
`controller_identity_fingerprint`; `HostLeaseReleaseV1.lease_digest`,
`released_by.subject`, `released_at`). A real store computes the digest with
Foundation's `lease_digest`; the fake reads `lease.digest`.
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lane3_lease_store import (  # noqa: E402
    MAX_RELEASE_SKEW_SECONDS,
    MAX_TTL_SECONDS,
    HeldLease,
    LeaseStanding,
    ReprovisionMarker,
    StoreRefused,
)

from tests.unit.lane3_e10_support import make_receipt  # noqa: E402

EPOCH = datetime(2026, 11, 17, 5, 0, 0, tzinfo=UTC)
PRINCIPAL = "repo:dotmac-tech/lane3-exposure-execution:ref:refs/heads/main"


class Backing:
    """The shared state behind a store: the rows, the clock and the row lock.

    Several `FakeStore` HANDLES may be opened onto one `Backing`, as several
    connections open onto one database. Exclusivity lives here, in the shared
    lock, never in any one handle.
    """

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.rows: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()


class FakeStore:
    """One handle onto a `Backing`: exclusive by the backing's lock, on its clock."""

    def __init__(
        self,
        *,
        authority: str = "database",
        now: datetime = EPOCH,
        backing: Backing | None = None,
    ) -> None:
        self.time_authority = authority
        self.backing = backing if backing is not None else Backing(now)
        self.covers_calls: list[tuple[str, datetime]] = []
        self.releases: list[tuple[Any, bool]] = []

    @property
    def _now(self) -> datetime:
        return self.backing.now

    @property
    def _rows(self) -> dict[str, dict[str, Any]]:
        return self.backing.rows

    @property
    def _lock(self) -> threading.Lock:
        return self.backing.lock

    def database_time(self) -> datetime:
        return self.backing.now

    def advance(self, seconds: float) -> None:
        self.backing.now += timedelta(seconds=seconds)

    def acquire_exclusive(
        self,
        *,
        host_id: str,
        authorization_run: str,
        controller_identity: Any,
        ttl_seconds: int,
        reprovision: ReprovisionMarker | None = None,
    ) -> HeldLease:
        if (
            type(ttl_seconds) is not int
            or ttl_seconds < 1
            or ttl_seconds > MAX_TTL_SECONDS
        ):
            raise StoreRefused("lease-ttl-invalid")
        with self._lock:
            row = self._rows.get(host_id)
            if row is not None:
                live = not row["released"] and self._now < row["expires_at"]
                if live:
                    raise StoreRefused("lease-held-by-another-run")
                mutated = (
                    row["mutated"] if row["released"] else True  # expired unreleased
                )
                if mutated and (reprovision is None or reprovision.host_id != host_id):
                    raise StoreRefused("lease-host-mutated")
            expires = self._now + timedelta(seconds=ttl_seconds)
            lease = types.SimpleNamespace(
                controller_identity_fingerprint=controller_identity,
                authorization_run_id=authorization_run,
                target=host_id,
                workload_principal=PRINCIPAL,
                expires_at=expires.isoformat(),
                digest=f"sha256:{host_id}:{authorization_run}:{self._now.isoformat()}",
            )
            self._rows[host_id] = {
                "run": authorization_run,
                "expires_at": expires,
                "released": False,
                "mutated": None,
                "released_at": None,
                "lease": lease,
            }
            return HeldLease(
                lease=lease,  # type: ignore[arg-type]
                handle=host_id,
                acquired_at=self._now,
                expires_at=expires,
            )

    def covers(self, held: HeldLease, *, authorization_run: str) -> datetime:
        row = self._rows[held.handle]
        self.covers_calls.append((authorization_run, self._now))
        if row["released"] or self._now >= row["expires_at"]:
            raise StoreRefused("lease-expired")
        if row["run"] != authorization_run:
            raise StoreRefused("lease-foreign-run")
        return self._now

    def release(self, held: HeldLease, *, release: Any, host_mutated: bool) -> str:
        with self._lock:
            row = self._rows[held.handle]
            if row["released"]:
                raise StoreRefused("lease-already-released")
            if self._now >= row["expires_at"]:
                raise StoreRefused("lease-expired")
            lease = row["lease"]
            if (
                release.lease_digest != lease.digest
                or release.released_by.subject != lease.workload_principal
                or release.controller_identity_fingerprint
                != lease.controller_identity_fingerprint
            ):
                raise StoreRefused("release-not-this-lease")
            stamped = datetime.fromisoformat(release.released_at)
            if abs((stamped - self._now).total_seconds()) > MAX_RELEASE_SKEW_SECONDS:
                raise StoreRefused("release-clock-skew")
            row["released"] = True
            row["mutated"] = host_mutated
            row["released_at"] = self._now  # STORE time, whatever the record says
            self.releases.append((release, host_mutated))
            return "release-digest"

    def standing(self, host_id: str) -> LeaseStanding:
        row = self._rows[host_id]
        if row["released"]:
            return LeaseStanding.RELEASED
        if self._now >= row["expires_at"]:
            return LeaseStanding.EXPIRED_MUTATED
        return LeaseStanding.HELD

    def released_at(self, host_id: str) -> datetime | None:
        return self._rows[host_id]["released_at"]


def release_for(store: Any, held: HeldLease, **overrides: Any) -> types.SimpleNamespace:
    """A release stand-in that matches `held` unless told otherwise."""
    fields: dict[str, Any] = {
        "lease_digest": held.lease.digest,
        "released_by": types.SimpleNamespace(subject=held.lease.workload_principal),
        "controller_identity_fingerprint": held.lease.controller_identity_fingerprint,
        "released_at": store.database_time().isoformat(),
    }
    fields.update(overrides)
    record = types.SimpleNamespace(**fields)
    record.as_document = lambda: {"release": "stand-in"}
    return record


def factory(cls: Callable[..., Any] = FakeStore) -> Callable[..., Any]:
    """`make(handle=None)`: a store, or a NEW handle onto `handle`'s backing."""

    def make(handle: Any = None) -> Any:
        if handle is None:
            return cls()
        return cls(backing=handle.backing)

    return make


def _acquire(store: Any, host: str = "host-a", run: str = "run-1", ttl: int = 60):
    return store.acquire_exclusive(
        host_id=host,
        authorization_run=run,
        controller_identity="fp",
        ttl_seconds=ttl,
    )


Case = Callable[[Callable[[], Any], Callable[[Any, float], None]], None]


def _raises(code: str, call: Callable[[], Any]) -> None:
    try:
        call()
    except StoreRefused as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return
    raise AssertionError(f"expected {code}, but nothing was refused")


def case_exclusive_under_concurrent_acquires(make_store: Any, advance: Any) -> None:
    """One handle PER THREAD onto one shared store.

    A store that only serialises the calls of one handle (a Python lock inside the
    object) admits one holder per handle and fails here; only exclusivity held in
    the shared store, as a row lock is, admits exactly one.
    """
    base = make_store()
    handles = [make_store(base) for _ in range(8)]
    outcomes: list[str] = []
    barrier = threading.Barrier(len(handles))

    def attempt(index: int) -> None:
        barrier.wait()
        try:
            _acquire(handles[index], run=f"run-{index}")
            outcomes.append("held")
        except StoreRefused:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert outcomes.count("held") == 1
    assert outcomes.count("refused") == 7


def case_covers_is_judged_on_store_time(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store)
    assert store.covers(held, authorization_run="run-1") == store.database_time()
    _raises("lease-foreign-run", lambda: store.covers(held, authorization_run="x"))
    advance(store, 61)
    _raises("lease-expired", lambda: store.covers(held, authorization_run="run-1"))


def case_expiry_is_a_mutated_host_never_a_clean_release(
    make_store: Any, advance: Any
) -> None:
    store = make_store()
    held = _acquire(store)
    advance(store, 61)
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED
    _raises(
        "lease-expired",
        lambda: store.release(
            held, release=release_for(store, held), host_mutated=False
        ),
    )
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED


def case_no_reacquire_after_expiry(make_store: Any, advance: Any) -> None:
    store = make_store()
    _acquire(store)
    advance(store, 61)
    _raises("lease-host-mutated", lambda: _acquire(store, run="run-2"))
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED


def case_no_reacquire_after_a_mutated_release(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store)
    store.release(held, release=release_for(store, held), host_mutated=True)
    _raises("lease-host-mutated", lambda: _acquire(store, run="run-2"))
    assert store.standing("host-a") is LeaseStanding.RELEASED


def case_an_untouched_release_may_be_reacquired(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store)
    store.release(held, release=release_for(store, held), host_mutated=False)
    assert _acquire(store, run="run-2") is not None


def case_only_an_explicit_reprovision_marker_reopens_a_mutated_host(
    make_store: Any, advance: Any
) -> None:
    store = make_store()
    _acquire(store)
    advance(store, 61)
    other = ReprovisionMarker("host-other", make_receipt(host_id="host-other"))
    _raises(
        "lease-host-mutated",
        lambda: store.acquire_exclusive(
            host_id="host-a",
            authorization_run="run-2",
            controller_identity="fp",
            ttl_seconds=60,
            reprovision=other,
        ),
    )
    marker = ReprovisionMarker("host-a", make_receipt(receipt_ref="e10-receipt-2"))
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-2",
        controller_identity="fp",
        ttl_seconds=60,
        reprovision=marker,
    )
    assert store.standing("host-a") is LeaseStanding.HELD
    assert held.lease.authorization_run_id == "run-2"


def case_only_one_release(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store)
    store.release(held, release=release_for(store, held), host_mutated=True)
    _raises(
        "lease-already-released",
        lambda: store.release(
            held, release=release_for(store, held), host_mutated=False
        ),
    )
    assert len(getattr(store, "releases", [1])) == 1


def case_a_release_must_match_the_held_lease(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store)
    wrong = {
        "digest": {"lease_digest": "sha256:" + "0" * 64},
        "principal": {"released_by": types.SimpleNamespace(subject="someone-else")},
        "controller": {"controller_identity_fingerprint": "another-key"},
    }
    for overrides in wrong.values():
        _raises(
            "release-not-this-lease",
            lambda o=overrides: store.release(
                held, release=release_for(store, held, **o), host_mutated=True
            ),
        )
    assert store.standing("host-a") is LeaseStanding.HELD


def case_the_lease_document_expiry_is_the_rows(make_store: Any, advance: Any) -> None:
    store = make_store()
    held = _acquire(store, ttl=120)
    assert datetime.fromisoformat(held.lease.expires_at) == held.expires_at
    assert held.expires_at - held.acquired_at == timedelta(seconds=120)


def case_the_ttl_is_capped(make_store: Any, advance: Any) -> None:
    store = make_store()
    for ttl in (0, -5, MAX_TTL_SECONDS + 1, 10**9, True, 1.5):
        _raises("lease-ttl-invalid", lambda t=ttl: _acquire(store, ttl=t))
    assert _acquire(store, ttl=MAX_TTL_SECONDS) is not None


def case_released_at_is_store_time_not_the_runners(
    make_store: Any, advance: Any
) -> None:
    store = make_store()
    held = _acquire(store)
    advance(store, 10)
    # A runner whose clock is hours off cannot get its stamp accepted...
    runner_clock = (store.database_time() + timedelta(hours=5)).isoformat()
    _raises(
        "release-clock-skew",
        lambda: store.release(
            held,
            release=release_for(store, held, released_at=runner_clock),
            host_mutated=True,
        ),
    )
    # ...and an accepted release is stamped on STORE time even if the record's own
    # stamp is slightly off.
    slightly_off = (store.database_time() - timedelta(seconds=5)).isoformat()
    store.release(
        held,
        release=release_for(store, held, released_at=slightly_off),
        host_mutated=True,
    )
    assert store.released_at("host-a") == store.database_time()


CASES: dict[str, Case] = {
    name.removeprefix("case_"): fn
    for name, fn in sorted(globals().items())
    if name.startswith("case_")
}
