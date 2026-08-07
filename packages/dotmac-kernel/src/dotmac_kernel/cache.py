"""Cache keys that carry their scope — the ONE place a cache key is built.

Every cached value in a multi-tenant system belongs to exactly one scope: a
tenant's, or the deployment's. Getting that wrong is not a performance bug, it
is a cross-tenant data leak with a green test suite — tenant B reads the entry
tenant A populated, and nothing anywhere raises.

## Why a TYPE and not a string parameter

The obvious design is `cache_key(*parts, tenant_id: UUID | None)`. It fails in
the way that matters: `None` is a legitimate-looking value, so "I forgot the
tenant" and "this is deliberately deployment-wide" produce the SAME key, and the
platform entry silently becomes the bucket every unscoped read lands in.

Scope is therefore a type. `PlatformScope()` is a separately NAMED global path,
not the absence of a tenant, and omitting `scope=` is a `TypeError` at the call
site rather than a plausible key at runtime. The two segments are structurally
different — `t=<uuid>` versus the bare literal `platform` — so no tenant
identifier can ever occupy the platform entry, however the parts are composed.

## Versioning is how invalidation stays honest

`version=` adds a segment, so bumping it retires an entire generation of entries
at once without a delete sweep. That matters for values derived from a mutable
declaration set (feature-flag evaluations, module availability): a targeted
delete has to enumerate what it invalidates and is wrong the moment the
derivation changes, while a version bump is correct by construction.

## Shape reference, and the one to avoid

`dotmac_kernel.middleware.rate_limit._rate_limit_key` was already this pattern
before this module existed (scope first, `platform` a named literal). The
counter-example is a single-scope product's settings cache — `PREFIX:domain:key`,
no scope segment — which is correct in a codebase whose settings table has one
scope and catastrophic in this one, where `domain_settings` is tenant-scoped.
`settings_resolver`'s docstring now says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

# The separator. Chosen because a UUID, a flag code and a module code can none
# of them contain it, so no combination of parts can forge another key's shape.
_SEPARATOR = ":"

# The platform scope's segment. A bare literal, deliberately NOT parseable as a
# tenant id — see the module docstring.
_PLATFORM_SEGMENT = "platform"


@dataclass(frozen=True, slots=True)
class TenantScope:
    """A value belonging to exactly one tenant."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class PlatformScope:
    """A value belonging to the deployment, not to any tenant.

    An explicit, named path — never "no tenant". Anything reachable by a
    deployment-wide read is by definition not tenant data, and saying so takes
    a distinct type so the claim is visible at the call site.
    """


Scope = TenantScope | PlatformScope


def scope_segment(scope: Scope) -> str:
    """The key segment for `scope`."""
    if isinstance(scope, TenantScope):
        return f"t={scope.tenant_id}"
    return _PLATFORM_SEGMENT


def cache_key(*parts: str, scope: Scope, version: str | int | None = None) -> str:
    """Build a cache key. `scope` is keyword-only and has NO default.

    >>> cache_key("flags", "billing.new_flow", scope=PlatformScope())
    'flags:billing.new_flow:platform'

    Parts are the value's identity (what is cached); the scope segment is who it
    belongs to and always comes last, so a key's owner is readable off the end
    of it in a cache dump. `version`, when given, follows the scope — bumping it
    retires a whole generation of entries.
    """
    if not parts:
        raise ValueError("cache_key requires at least one part naming the value")
    for part in parts:
        if not part:
            raise ValueError("cache_key parts must be non-empty")
        if _SEPARATOR in part:
            raise ValueError(
                f"cache key part {part!r} contains {_SEPARATOR!r} — a part that "
                "can inject a separator can forge another key's shape"
            )
    segments = [*parts, scope_segment(scope)]
    if version is not None:
        segments.append(f"v={version}")
    return _SEPARATOR.join(segments)


class CacheStore(Protocol):
    """The swap seam for a shared backend (e.g. Redis).

    Deliberately tiny and key-agnostic: the store never builds a key, so a
    future Redis implementation inherits this module's key model instead of
    inventing one — which is exactly how a scope segment gets lost.
    """

    def get(self, key: str) -> object | None:
        """The cached value, or None if absent."""
        ...

    def set(self, key: str, value: object, *, ttl_seconds: int | None = None) -> None:
        """Store `value` under `key`."""
        ...

    def delete_prefix(self, prefix: str) -> int:
        """Drop every entry whose key starts with `prefix`. Returns the count.

        Prefix-based, because the keys this module builds put identity first —
        so "everything about this flag" is a prefix, while "everything of this
        tenant's" deliberately is NOT (the scope segment is last). Retiring a
        tenant's entries is a version bump, not a scan.
        """
        ...


class MemoryCache:
    """Process-local `CacheStore`, LRU-bounded.

    The default store: correct for a single-process deployment and for tests,
    and it makes the seam real before any Redis exists. Bounded because an
    unbounded per-tenant cache in a multi-tenant process is a memory leak with
    a tenant-count trigger.
    """

    def __init__(self, *, max_keys: int = 10_000) -> None:
        from collections import OrderedDict

        self.max_keys = max_keys
        self._entries: OrderedDict[str, object] = OrderedDict()

    def get(self, key: str) -> object | None:
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]

    def set(self, key: str, value: object, *, ttl_seconds: int | None = None) -> None:
        # TTL is accepted and ignored: this store's lifetime is the process, and
        # silently pretending to honour an expiry would be worse than not
        # honouring it visibly. A Redis store implements it for real.
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_keys:
            self._entries.popitem(last=False)

    def delete_prefix(self, prefix: str) -> int:
        doomed = [key for key in self._entries if key.startswith(prefix)]
        for key in doomed:
            del self._entries[key]
        return len(doomed)

    def clear(self) -> None:
        self._entries.clear()


__all__ = [
    "CacheStore",
    "MemoryCache",
    "PlatformScope",
    "Scope",
    "TenantScope",
    "cache_key",
    "scope_segment",
]
