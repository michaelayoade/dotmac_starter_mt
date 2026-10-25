"""Limiter policy over an abstract lock store (dotmac-device-cli, ADR-0077)."""

from __future__ import annotations

import pytest
from dotmac_device_cli.core.limiter import (
    DeviceBusy,
    LimiterConfig,
    Priority,
    acquire_slot,
    busy_message,
    intent_key,
    is_busy_message,
    is_login_failed_message,
    is_login_refusal_message,
    login_failed_message,
    slot_key,
    slot_order,
    wait_budget,
)
from dotmac_device_cli.core.profile import Limits
from dotmac_device_cli.testing import FakeClock, InMemoryLockBackend

# KEEP IN SYNC WITH SUB. These vectors were computed with the derivation in
# dotmac_sub `app/services/network/olt_ssh_limiter.py::_signed_key`
# (blake2b, 8-byte digest, big-endian, signed) at release 7999c3019. If this
# test must change, Sub's limiter must change in the same step, or the two will
# stop sharing slots in the shared database and briefly allow twice the sessions.
# Sub pins no vectors of its own (it only tests that keys are distinct): these
# must be mirrored into Sub when it adopts this package. The device key must be
# str(olt.id).
SLOT_VECTORS = {
    "00000000-0000-4000-8000-000000000001": (
        -3148890172339245725,
        -9068375865187576960,
        -1039721830728024854,
    ),
    "lab-olt": (-8678444213299380193, 1909821310459583165, -318973743125864412),
    "203.0.113.7-test": (
        8375931043202659789,
        -4874932496492983623,
        -647553131079769680,
    ),
}
INTENT_VECTORS = {
    "00000000-0000-4000-8000-000000000001": -7864250139092424918,
    "lab-olt": 1790200665425164038,
    "203.0.113.7-test": -4163376672186175071,
}
LIMITS = Limits(2, 1, 2.0, 2.0, 3, 2)


@pytest.mark.parametrize("key", sorted(SLOT_VECTORS))
def test_lock_keys_are_byte_identical_to_sub(key: str) -> None:
    assert tuple(slot_key(key, i) for i in range(3)) == SLOT_VECTORS[key]
    assert intent_key(key) == INTENT_VECTORS[key]


def test_slot_order_by_priority() -> None:
    assert slot_order(Priority.WRITE, 4, 1) == [3, 2, 1, 0]
    assert slot_order(Priority.INTERACTIVE, 4, 1) == [0, 1, 2, 3]
    assert slot_order(Priority.BACKGROUND, 4, 1) == [0, 1, 2]
    assert slot_order(Priority.BACKGROUND, 1, 0) == [0]


def test_host_may_only_lower_the_profile_limit() -> None:
    assert LimiterConfig.limit_for(LIMITS) == 2
    assert LimiterConfig.limit_for(LIMITS, host_max=1) == 1
    assert LimiterConfig.limit_for(LIMITS, host_max=50) == 2
    assert LimiterConfig.reserved_for(LIMITS, 1) == 0  # never reserve every slot


def test_wait_budget_rules() -> None:
    cfg = LimiterConfig()
    assert wait_budget(cfg, Priority.WRITE, now=0, deadline=None, nested=False) == 45
    assert (
        wait_budget(cfg, Priority.INTERACTIVE, now=0, deadline=None, nested=False) == 15
    )
    assert (
        wait_budget(cfg, Priority.BACKGROUND, now=0, deadline=None, nested=False) == 0
    )
    assert wait_budget(cfg, Priority.WRITE, now=0, deadline=None, nested=True) == 0
    # 30 s of the caller's deadline are left for the work itself
    assert wait_budget(cfg, Priority.WRITE, now=0, deadline=40, nested=False) == 10
    assert wait_budget(cfg, Priority.WRITE, now=0, deadline=20, nested=False) == 0


def _acquire(
    backend: InMemoryLockBackend, clock: FakeClock, priority: Priority, wait: float = 0
):
    return acquire_slot(
        backend.store,
        device_key="lab-olt",
        limit=2,
        reserved=1,
        priority=priority,
        wait_seconds=wait,
        clock=clock,
        jitter=lambda lo, _hi: lo,
    )


def test_no_more_than_limit_sessions_and_release_frees_a_slot() -> None:
    backend, clock = InMemoryLockBackend(), FakeClock()
    a = _acquire(backend, clock, Priority.INTERACTIVE)
    b = _acquire(backend, clock, Priority.INTERACTIVE)
    assert {a.slot_index, b.slot_index} == {0, 1}
    with pytest.raises(DeviceBusy) as exc:
        _acquire(backend, clock, Priority.INTERACTIVE)
    assert is_busy_message(str(exc.value))
    a.release()
    a.release()  # idempotent
    assert _acquire(backend, clock, Priority.INTERACTIVE).slot_index == a.slot_index


def test_background_leaves_the_reserved_slot_and_never_waits() -> None:
    backend, clock = InMemoryLockBackend(), FakeClock()
    first = _acquire(backend, clock, Priority.BACKGROUND)
    assert first.slot_index == 0
    started = clock.monotonic()
    with pytest.raises(DeviceBusy):
        _acquire(backend, clock, Priority.BACKGROUND, wait=30)  # wait is ignored
    assert clock.monotonic() == started
    # the reserved slot is still there for a writer
    assert _acquire(backend, clock, Priority.WRITE).slot_index == 1


def test_background_skips_when_a_writer_is_queued() -> None:
    backend, clock = InMemoryLockBackend(), FakeClock()
    _acquire(backend, clock, Priority.WRITE)
    _acquire(backend, clock, Priority.WRITE)
    with pytest.raises(DeviceBusy):
        _acquire(backend, clock, Priority.WRITE, wait=2)  # leaves its intent behind
    # the failed waiter closed its store, so its intent is gone: no stale block
    assert backend.held_exclusive() == {slot_key("lab-olt", 0), slot_key("lab-olt", 1)}


def test_a_waiting_writer_holds_intent_so_background_yields() -> None:
    backend, clock = InMemoryLockBackend(), FakeClock()
    waiter = backend.store()
    assert waiter.try_lock_shared(intent_key("lab-olt"))
    with pytest.raises(DeviceBusy, match="higher-priority"):
        _acquire(backend, clock, Priority.BACKGROUND)


def test_waiter_gets_a_slot_when_one_frees_within_budget() -> None:
    backend, clock = InMemoryLockBackend(), FakeClock()
    a = _acquire(backend, clock, Priority.INTERACTIVE)
    _acquire(backend, clock, Priority.INTERACTIVE)

    real_sleep = clock.sleep

    def sleep_then_free(seconds: float) -> None:
        real_sleep(seconds)
        a.release()

    clock.sleep = sleep_then_free  # type: ignore[method-assign]
    got = _acquire(backend, clock, Priority.INTERACTIVE, wait=15)
    assert got.slot_index == a.slot_index
    assert got.wait_seconds > 0


def test_fails_closed_when_the_store_is_unreachable() -> None:
    def broken():
        raise ConnectionError("lock database down")

    with pytest.raises(DeviceBusy, match="unavailable"):
        acquire_slot(
            broken, device_key="x", limit=2, reserved=1,
            priority=Priority.WRITE, wait_seconds=1, clock=FakeClock(),
        )  # fmt: skip


def test_no_factory_is_a_type_error_never_a_silent_no_op() -> None:
    with pytest.raises(TypeError):
        acquire_slot(
            None, device_key="x", limit=2, reserved=1,
            priority=Priority.WRITE, wait_seconds=1, clock=FakeClock(),
        )  # fmt: skip


def test_message_prefixes_and_refusal_patterns() -> None:
    assert busy_message("x") == "olt_busy: x"
    assert login_failed_message("x") == "olt_login_failed: x"
    assert is_login_failed_message(login_failed_message("x"))
    assert is_login_refusal_message("Error reading SSH protocol banner")
    assert is_login_refusal_message("[Errno 104] Connection reset by peer")
    assert not is_login_refusal_message("Authentication failed.")
