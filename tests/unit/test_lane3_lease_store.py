"""E7 / U5: the lease-store contract the launcher requires, and the refusal of
any store that cannot supply database time."""

from __future__ import annotations

import pathlib
import sys
import threading

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_lease_store as contract  # noqa: E402

from tests.unit.lane3_e10_support import make_receipt  # noqa: E402
from tests.unit.lane3_store_support import CASES, FakeStore, factory  # noqa: E402


def test_a_store_with_database_time_is_accepted() -> None:
    store = FakeStore()
    assert contract.require_database_time(store) is store


@pytest.mark.parametrize("authority", ["local", "runner", "", "DATABASE"])
def test_a_store_that_does_not_declare_database_time_is_refused(
    authority: str,
) -> None:
    with pytest.raises(contract.StoreRefused) as refusal:
        contract.require_database_time(FakeStore(authority=authority))
    assert refusal.value.code == "store-lacks-database-time"


def test_the_file_backed_store_is_not_a_lease_store_for_this_lane(
    tmp_path: pathlib.Path,
) -> None:
    """A directory of JSON records has no database clock: the interface is absent."""
    for candidate in (str(tmp_path), tmp_path, object()):
        with pytest.raises(contract.StoreRefused) as refusal:
            contract.require_database_time(candidate)
        assert refusal.value.code == "store-lacks-interface"


def test_a_store_whose_clock_is_naive_or_broken_is_refused() -> None:
    from datetime import datetime

    naive = FakeStore(now=datetime(2026, 11, 17, 5, 0, 0))
    with pytest.raises(contract.StoreRefused) as refusal:
        contract.require_database_time(naive)
    assert refusal.value.code == "store-lacks-database-time"

    class Broken(FakeStore):
        def database_time(self):
            raise RuntimeError("database unreachable")

    with pytest.raises(contract.StoreRefused) as refusal:
        contract.require_database_time(Broken())
    assert refusal.value.code == "store-lacks-database-time"


def test_the_contract_names_exactly_the_standings_the_destroy_gate_reads() -> None:
    assert {s.value for s in contract.LeaseStanding} == {
        "held",
        "released",
        "expired_mutated",
    }


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_reference_fake_passes_each_conformance_case(name: str) -> None:
    """One test per rule, so a store that breaks one rule fails by its name."""
    CASES[name](factory(), lambda store, seconds: store.advance(seconds))


def test_the_conformance_cases_cover_every_rule_in_the_contract() -> None:
    assert set(CASES) == {
        "exclusive_under_concurrent_acquires",
        "covers_is_judged_on_store_time",
        "expiry_is_a_mutated_host_never_a_clean_release",
        "no_reacquire_after_expiry",
        "no_reacquire_after_a_mutated_release",
        "an_untouched_release_may_be_reacquired",
        "only_an_explicit_reprovision_marker_reopens_a_mutated_host",
        "only_one_release",
        "a_release_must_match_the_held_lease",
        "the_lease_document_expiry_is_the_rows",
        "the_ttl_is_capped",
        "released_at_is_store_time_not_the_runners",
    }


# A store with the old behaviour must FAIL the new cases: these are the planted
# violations, so the suite is shown to bite rather than assumed to.


class _WipesHistory(FakeStore):
    """Overwrites an expired or mutated row, as the first fake did."""

    def acquire_exclusive(self, **kwargs):
        host = kwargs["host_id"]
        row = self._rows.get(host)
        if row is not None and (row["released"] or self._now >= row["expires_at"]):
            del self._rows[host]
        return super().acquire_exclusive(**kwargs)


class _NoLock(FakeStore):
    """Checks then writes with no row lock."""

    def acquire_exclusive(self, **kwargs):
        import time

        host = kwargs["host_id"]
        row = self._rows.get(host)
        live = row and not row["released"] and self._now < row["expires_at"]
        time.sleep(0.05)
        if live:
            raise contract.StoreRefused("lease-held-by-another-run")
        self._rows.pop(host, None)
        return FakeStore.acquire_exclusive(self, **kwargs)


class _PythonLockOnly(FakeStore):
    """Serialises its OWN calls with a Python lock and shares nothing: each handle
    has private state, so there is no exclusivity between handles at all."""

    def __init__(self, **kwargs):
        kwargs.pop("backing", None)
        super().__init__(**kwargs)
        self._own = threading.Lock()

    def acquire_exclusive(self, **kwargs):
        with self._own:
            return super().acquire_exclusive(**kwargs)


@pytest.mark.parametrize(
    ("broken", "case"),
    [
        (_WipesHistory, "no_reacquire_after_expiry"),
        (_WipesHistory, "no_reacquire_after_a_mutated_release"),
        (_NoLock, "exclusive_under_concurrent_acquires"),
        (_PythonLockOnly, "exclusive_under_concurrent_acquires"),
    ],
)
def test_a_store_that_breaks_a_rule_fails_that_case(broken, case: str) -> None:
    with pytest.raises(AssertionError):
        CASES[case](factory(broken), lambda store, seconds: store.advance(seconds))


def test_handles_onto_one_backing_share_exclusivity_and_the_clock() -> None:
    first = FakeStore()
    second = FakeStore(backing=first.backing)
    held = first.acquire_exclusive(
        host_id="host-a",
        authorization_run="run-1",
        controller_identity="fp",
        ttl_seconds=60,
    )
    with pytest.raises(contract.StoreRefused):
        second.acquire_exclusive(
            host_id="host-a",
            authorization_run="run-2",
            controller_identity="fp",
            ttl_seconds=60,
        )
    first.advance(61)
    assert second.database_time() == first.database_time()
    assert second.standing("host-a") is contract.LeaseStanding.EXPIRED_MUTATED
    assert held.expires_at <= second.database_time()


# ── the reprovision marker cites a typed E10 receipt, never a free string ───


def test_a_marker_with_a_free_string_instead_of_a_receipt_is_refused() -> None:
    for citation in ("e10-receipt-1", None, {"receipt_ref": "x"}):
        with pytest.raises(contract.StoreRefused) as refusal:
            contract.ReprovisionMarker("host-a", citation)  # type: ignore[arg-type]
        assert refusal.value.code == "reprovision-receipt-required"


def test_a_marker_citing_another_hosts_receipt_is_refused() -> None:
    with pytest.raises(contract.StoreRefused) as refusal:
        contract.ReprovisionMarker("host-a", make_receipt(host_id="host-b"))
    assert refusal.value.code == "reprovision-receipt-for-another-host"


def test_a_marker_citing_the_hosts_own_receipt_is_accepted() -> None:
    marker = contract.ReprovisionMarker("host-a", make_receipt())
    assert marker.e10_receipt.receipt_ref == "e10-receipt-0001"
