"""Per-device session limiter POLICY over an abstract lock store.

Policy lives here; storage lives in the host (Sub supplies a Postgres advisory
lock store). Every worker, container and script that opens a shell takes one
slot first, so a device never sees more than N concurrent logins.

Priority is WRITE > INTERACTIVE > BACKGROUND. BACKGROUND may use only the first
``N - reserved`` slots, never waits, and skips whenever anyone is queued. A
waiting WRITE/INTERACTIVE caller holds a shared "intent" lock; BACKGROUND must
first win it exclusively. A nested open for a device the caller already holds
never waits (no hold-and-wait). With the store unreachable the limiter FAILS
CLOSED: no lock, no session.

KEEP IN SYNC WITH SUB: ``slot_key``/``intent_key`` are copied byte for byte from
Sub's ``olt_ssh_limiter._signed_key`` so old and new code share slots in the
same database during the swap. A different derivation would briefly allow twice
the sessions. ``tests/unit/test_device_cli_limiter.py`` pins known vectors HERE;
Sub has none (it only tests that keys are distinct), so Sub must mirror them
when it adopts this package. ``device_key`` MUST be ``str(olt.id)``.
"""

from __future__ import annotations

import contextlib
import hashlib
import random
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol

from dotmac_device_cli.core.channel import Clock
from dotmac_device_cli.core.profile import Limits

BUSY_PREFIX = "olt_busy:"
LOGIN_FAILED_PREFIX = "olt_login_failed:"
_SLOT_NAMESPACE = "olt-ssh-slot"
_INTENT_NAMESPACE = "olt-ssh-intent"
_MAX_SLOTS = 8
_JITTER = random.SystemRandom()  # backoff jitter only, not security


class Priority(IntEnum):
    """Lower value = higher priority."""

    WRITE = 0
    INTERACTIVE = 1
    BACKGROUND = 2


class DeviceBusy(TimeoutError):
    """No slot within the wait budget. Raised before any socket is opened."""


class LockStore(Protocol):
    """One store object per lease (a pinned connection in the host)."""

    def try_lock(self, key: int) -> bool: ...

    def unlock(self, key: int) -> None: ...

    def try_lock_shared(self, key: int) -> bool: ...

    def unlock_shared(self, key: int) -> None: ...

    def close(self) -> None:
        """Release every lock this store holds."""
        ...


# ── key derivation (byte-identical to Sub) ───────────────────────────────────


def _signed_key(namespace: str, device_key: str, index: int | None = None) -> int:
    label = f"{namespace}:{device_key}" + (f":{index}" if index is not None else "")
    digest = hashlib.blake2b(label.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def slot_key(device_key: str, index: int) -> int:
    return _signed_key(_SLOT_NAMESPACE, device_key, index)


def intent_key(device_key: str) -> int:
    return _signed_key(_INTENT_NAMESPACE, device_key)


# ── messages and refusal patterns ────────────────────────────────────────────

_LOGIN_REFUSAL = (
    re.compile(r"transport shut down or saw EOF", re.IGNORECASE),
    re.compile(r"Error reading SSH protocol banner", re.IGNORECASE),
    re.compile(r"Errno 104|Connection reset by peer", re.IGNORECASE),
    re.compile(r"No existing session", re.IGNORECASE),
    re.compile(r"Authentication timeout", re.IGNORECASE),
    re.compile(r"\bEOFError\b"),
)


def busy_message(detail: str) -> str:
    return f"{BUSY_PREFIX} {detail}"


def is_busy_message(message: object) -> bool:
    return BUSY_PREFIX in str(message or "")


def login_failed_message(detail: str) -> str:
    return f"{LOGIN_FAILED_PREFIX} {detail}"


def is_login_failed_message(message: object) -> bool:
    return LOGIN_FAILED_PREFIX in str(message or "")


def is_login_refusal_message(message: object) -> bool:
    text = str(message or "")
    return any(p.search(text) for p in _LOGIN_REFUSAL)


def is_auth_rejected_message(message: object) -> bool:
    text = str(message or "").lower()
    return "authentication failed" in text or "auth_rejected" in text


# ── configuration: a host may only LOWER the profile's limits ────────────────


@dataclass(frozen=True, slots=True)
class LimiterConfig:
    wait_write_seconds: int = 45
    wait_interactive_seconds: int = 15
    work_reserve_seconds: float = 30.0

    @staticmethod
    def limit_for(limits: Limits, host_max: int | None = None) -> int:
        value = limits.session_slots
        if host_max is not None:
            value = min(value, host_max)  # lower only, never raise
        return max(1, min(int(value), _MAX_SLOTS))

    @staticmethod
    def reserved_for(limits: Limits, limit: int) -> int:
        # Never reserve every slot: BACKGROUND must keep at least one.
        return max(0, min(int(limits.reserved_priority_slots), limit - 1))


def slot_order(priority: Priority, limit: int, reserved: int) -> list[int]:
    if priority is Priority.BACKGROUND:
        return list(range(max(1, limit - reserved)))
    if priority is Priority.WRITE:
        return list(range(limit - 1, -1, -1))  # writers take reserved slots first
    return list(range(limit))


def wait_budget(
    config: LimiterConfig,
    priority: Priority,
    *,
    now: float,
    deadline: float | None,
    nested: bool,
) -> float:
    if nested or priority is Priority.BACKGROUND:
        return 0.0  # never wait while holding, and background never waits
    budget = float(
        config.wait_write_seconds
        if priority is Priority.WRITE
        else config.wait_interactive_seconds
    )
    if deadline is not None:
        budget = min(budget, max(0.0, deadline - now - config.work_reserve_seconds))
    return budget


# ── acquisition ──────────────────────────────────────────────────────────────


class SlotLease:
    """One held slot. Release is idempotent and never raises."""

    def __init__(
        self,
        store: LockStore,
        key: int,
        device_key: str,
        slot_index: int,
        wait_seconds: float,
    ) -> None:
        self._store: LockStore | None = store
        self._key = key
        self.device_key = device_key
        self.slot_index = slot_index
        self.wait_seconds = wait_seconds
        self._lock = threading.Lock()

    @property
    def released(self) -> bool:
        return self._store is None

    def release(self) -> None:
        with self._lock:
            store, self._store = self._store, None
        if store is None:
            return
        try:
            with contextlib.suppress(Exception):  # closing releases it anyway
                store.unlock(self._key)
        finally:
            with contextlib.suppress(Exception):
                store.close()

    def __enter__(self) -> SlotLease:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


StoreFactory = Callable[[], LockStore]


def acquire_slot(
    store_factory: StoreFactory | None,
    *,
    device_key: str,
    limit: int,
    reserved: int,
    priority: Priority,
    wait_seconds: float,
    clock: Clock,
    jitter: Callable[[float, float], float] = _JITTER.uniform,
) -> SlotLease:
    """Take one slot or raise :class:`DeviceBusy`. There is no silent no-op."""
    if store_factory is None:
        raise TypeError(
            "a store factory is required; the limiter never no-ops silently"
        )
    started = clock.monotonic()
    try:
        store = store_factory()
    except Exception as exc:
        raise DeviceBusy(busy_message("session limiter store unavailable")) from exc

    try:
        intent = intent_key(device_key)
        if priority is Priority.BACKGROUND:
            if not store.try_lock(intent):
                raise DeviceBusy(
                    busy_message("a higher-priority request is waiting for a slot")
                )
            store.unlock(intent)
            wait_seconds = 0.0
        order = slot_order(priority, limit, reserved)
        deadline = started + max(0.0, wait_seconds)
        intent_held = False
        while True:
            for index in order:
                key = slot_key(device_key, index)
                if store.try_lock(key):
                    if intent_held:
                        store.unlock_shared(intent)
                    return SlotLease(
                        store, key, device_key, index, clock.monotonic() - started
                    )
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                raise DeviceBusy(
                    busy_message(
                        f"all {limit} session slot(s) for this device are in use"
                    )
                )
            if priority is not Priority.BACKGROUND and not intent_held:
                intent_held = store.try_lock_shared(intent)
            clock.sleep(min(jitter(0.5, 1.5), remaining))
    except BaseException:
        with contextlib.suppress(Exception):
            store.close()  # closing releases every lock this store holds
        raise
