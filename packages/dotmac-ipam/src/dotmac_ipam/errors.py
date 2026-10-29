"""Stable, fail-closed IPAM errors."""

from __future__ import annotations


class IpamError(ValueError):
    """Base error for a refused IPAM decision."""

    code = "ipam.refused"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class IpamNotFound(IpamError):
    """A tenant-local IPAM entity was not found."""

    code = "ipam.not_found"


class IpamConflict(IpamError):
    """An identity, reservation, assignment, or expected state conflicts."""

    code = "ipam.conflict"


class PoolExhausted(IpamError):
    """No allocatable address remains in the selected pool(s)."""

    code = "ipam.pool_exhausted"


class StalePreview(IpamError):
    """Evidence changed after preview; preview again."""

    code = "ipam.stale_preview"


class UnsafeRepair(IpamError):
    """The reviewed repair is no longer safe to apply."""

    code = "ipam.unsafe_repair"


__all__ = [
    "IpamConflict",
    "IpamError",
    "IpamNotFound",
    "PoolExhausted",
    "StalePreview",
    "UnsafeRepair",
]
