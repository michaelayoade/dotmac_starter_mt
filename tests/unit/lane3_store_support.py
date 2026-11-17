"""An in-memory lease store, and the conformance suite a real one must pass.

E7 / U5. The CP production-database store does not exist yet (separate
image-budget ruling); this fake models the contract in
`scripts/lane3_lease_store.py` with an injected database clock, and
:func:`conformance` is the behaviour any implementation must show. The CP-side
work should run it against its own store.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lane3_lease_store import (  # noqa: E402
    HeldLease,
    LeaseStanding,
    StoreRefused,
)

EPOCH = datetime(2026, 11, 17, 5, 0, 0, tzinfo=UTC)


class FakeStore:
    """Exclusive by 'row lock', judged on its own injected database clock."""

    def __init__(self, *, authority: str = "database", now: datetime = EPOCH) -> None:
        self.time_authority = authority
        self._now = now
        self._rows: dict[str, dict[str, Any]] = {}
        self.covers_calls: list[tuple[str, datetime]] = []
        self.releases: list[tuple[Any, bool]] = []

    # the database's own clock; the ONLY clock anything here reads
    def database_time(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def acquire_exclusive(
        self,
        *,
        host_id: str,
        authorization_run: str,
        controller_identity: Any,
        ttl_seconds: int,
    ) -> HeldLease:
        row = self._rows.get(host_id)
        if row and not row["released"] and self._now < row["expires_at"]:
            raise StoreRefused("lease-held-by-another-run")
        expires = self._now + timedelta(seconds=ttl_seconds)
        lease = types.SimpleNamespace(
            controller_identity_fingerprint=controller_identity,
            authorization_run_id=authorization_run,
            target=host_id,
        )
        self._rows[host_id] = {
            "run": authorization_run,
            "expires_at": expires,
            "released": False,
            "mutated": None,
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
        row = self._rows[held.handle]
        if row["released"] or self._now >= row["expires_at"]:
            raise StoreRefused("lease-expired")
        row["released"] = True
        row["mutated"] = host_mutated
        self.releases.append((release, host_mutated))
        return "release-digest"

    def standing(self, host_id: str) -> LeaseStanding:
        row = self._rows[host_id]
        if row["released"]:
            return LeaseStanding.RELEASED
        if self._now >= row["expires_at"]:
            return LeaseStanding.EXPIRED_MUTATED
        return LeaseStanding.HELD


def conformance(make_store: Any, advance: Any) -> None:
    """Behaviour every lease store must show. `advance(store, seconds)` moves its
    database clock (a real store advances by waiting or by a test hook)."""
    import pytest

    store = make_store()
    held = store.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="fp",
        ttl_seconds=60,
    )
    # 1. exclusive: a second holder is refused while the first is live
    with pytest.raises(StoreRefused):
        store.acquire_exclusive(
            host_id="host-a",
            authorization_run="run-2",
            controller_identity="fp",
            ttl_seconds=60,
        )
    # 2. covers is judged on store time and names the instant it judged
    assert store.covers(held, authorization_run="run-1") == store.database_time()
    with pytest.raises(StoreRefused):
        store.covers(held, authorization_run="run-other")
    assert store.standing("host-a") is LeaseStanding.HELD
    # 3. a lease nobody releases expires on STORE time as a MUTATED host,
    #    never as released or reusable
    advance(store, 61)
    with pytest.raises(StoreRefused):
        store.covers(held, authorization_run="run-1")
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED
    # 4. and an expired lease can never be released as untouched afterwards
    with pytest.raises(StoreRefused):
        store.release(held, release=object(), host_mutated=False)
    assert store.standing("host-a") is LeaseStanding.EXPIRED_MUTATED
    # 5. a live lease releases into the SAME store, recording host_mutated
    other = store.acquire_exclusive(
        host_id="host-b",
        authorization_run="run-3",
        controller_identity="fp",
        ttl_seconds=60,
    )
    store.release(other, release=object(), host_mutated=True)
    assert store.standing("host-b") is LeaseStanding.RELEASED
