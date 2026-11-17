"""E7 / U5: the lease-store contract the launcher requires, and the refusal of
any store that cannot supply database time."""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane3_lease_store as contract  # noqa: E402

from tests.unit.lane3_store_support import FakeStore, conformance  # noqa: E402


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


def test_the_reference_fake_passes_the_conformance_suite() -> None:
    conformance(FakeStore, lambda store, seconds: store.advance(seconds))
