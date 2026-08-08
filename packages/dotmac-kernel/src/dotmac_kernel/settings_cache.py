"""Read cache for resolved settings, scoped by construction.

`resolve_value` is on request paths that read the same handful of settings on
every call — branding, display formats, the registration policy — and each read
is one or two indexed queries. Caching them is worth doing and is also the
single most dangerous thing to get wrong in this subsystem, because a
cross-tenant cache leak raises no exception: tenant B is simply served the entry
tenant A populated, and the only evidence is in someone else's data.

So the scope is not a parameter this module could forget. Keys come from
`dotmac_kernel.cache.cache_key`, whose `scope` is keyword-only with no default
and whose segments a part cannot forge. `dotmac_erp` shipped the counter-example:
a key of `settings:{domain}:{key}` with no scope segment at all, which served
one organization's stored values to every other, deployment-wide, because Redis
is shared across workers. That defect is what this module's shape exists to make
unrepresentable.

## What is deliberately NOT cached

* **Secret settings.** Step 3 encrypts them at rest precisely so a database dump
  does not carry credentials; putting the DECRYPTED value in a cache — typically
  a shared Redis, often with weaker access control and no encryption at rest —
  gives most of that back. Secrets are also cold: they are read by a background
  job, not on a request path.
* **The `default=` shortcut.** `resolve_value(..., default=X)` for an
  unregistered key never touches the database, so there is nothing to save.

## Invalidation

The writer knows exactly what changed, so invalidation is an explicit act at the
write, not a TTL someone has to reason about:

* a TENANT write drops that tenant's entry only;
* a PLATFORM write drops **every** scope's entry for that (domain, key) — every
  tenant inherits the platform row when it has no row of its own, so a platform
  change silently changes what those tenants resolve.

That asymmetry is why keys put identity first and scope last: "this setting, all
scopes" is a prefix, and "everything of one tenant's" deliberately is not.

## Installation — and why the default is OFF

Nothing is cached until a deployment installs a store, and `create_app`
deliberately does not install one.

Invalidation is a delete, and a delete only reaches the process that performs
it. Under two or more workers — the normal production shape — worker A's write
invalidates worker A's memory while worker B keeps serving the old value until
its entry is evicted. "I changed the setting and it did not take effect, on some
requests" is worse than no cache at all, and it is the kind of fault that
reproduces once in ten tries.

`dotmac_kernel.flag_models` can default a `MemoryCache` on because a flag key
carries a `version` read from the database, so a stale worker's key becomes
unreachable the moment the version moves. A setting has no such version, so the
same default would be unsafe here.

A single-process deployment installs `MemoryCache()` and is correct. A
multi-worker deployment installs a SHARED store, so one worker's invalidation
reaches the others. Either is a deliberate act.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from uuid import UUID

from dotmac_kernel.cache import (
    CacheStore,
    PlatformScope,
    Scope,
    TenantScope,
    cache_key,
)

if TYPE_CHECKING:
    from dotmac_kernel.settings_models import SettingDomain

_NAMESPACE = "settings"


class _Miss:
    """Distinguishes "not cached" from a cached value of `None`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<settings cache MISS>"


MISS = _Miss()


def _scope(tenant_id: UUID | None) -> Scope:
    return TenantScope(tenant_id) if tenant_id is not None else PlatformScope()


def setting_cache_key(
    domain: SettingDomain | str, key: str, *, tenant_id: UUID | None
) -> str:
    """The cache key for one resolved setting.

    `k=` prefixes the setting key so no setting can collide with a future
    aggregate entry (`all`, say) in the same namespace, and so the two are
    distinguishable in a cache dump.
    """
    return cache_key(_NAMESPACE, str(domain), f"k={key}", scope=_scope(tenant_id))


def setting_key_prefix(domain: SettingDomain | str, key: str) -> str:
    """Every scope's entry for one setting — what a PLATFORM write invalidates.

    Derived from `setting_cache_key` by dropping the trailing scope segment,
    rather than re-assembled here: two independent renderings of one key shape
    is exactly how a prefix comes to miss the keys it is meant to drop.
    """
    built = setting_cache_key(domain, key, tenant_id=None)
    return built[: built.rindex(":") + 1]


# ── The process-active store ────────────────────────────────────────────────

_active_store: CacheStore | None = None


def install_settings_cache(store: CacheStore | None) -> None:
    """Install (or, with None, remove) the process-active settings cache."""
    global _active_store
    _active_store = store


def active_settings_cache() -> CacheStore | None:
    """The process-active store, or None when settings are not cached."""
    return _active_store


def cached(domain: SettingDomain | str, key: str, *, tenant_id: UUID | None) -> object:
    """A cached `(value, source)` pair, or `MISS`.

    Returns a sentinel rather than None, because `None` is a legitimate resolved
    value (an unset setting with no default) and treating it as a miss would
    make every such read hit the database forever.
    """
    store = _active_store
    if store is None:
        return MISS
    hit = store.get(setting_cache_key(domain, key, tenant_id=tenant_id))
    if hit is None:
        return MISS
    # Defensive copy on the way OUT as well as in: a json setting resolves to a
    # dict, and a caller that mutates what it was handed would otherwise corrupt
    # the entry for every later reader in the process.
    return copy.deepcopy(hit)


def store_resolved(
    domain: SettingDomain | str,
    key: str,
    *,
    tenant_id: UUID | None,
    value: object,
    source: str,
    is_secret: bool,
) -> None:
    """Cache one resolved `(value, source)`. A secret is silently not cached."""
    store = _active_store
    if store is None or is_secret:
        return
    store.set(
        setting_cache_key(domain, key, tenant_id=tenant_id),
        copy.deepcopy((value, source)),
    )


def invalidate(domain: SettingDomain | str, key: str, *, tenant_id: UUID | None) -> int:
    """Drop what a write to (domain, key, scope) invalidated. Returns the count.

    A platform write drops every scope's entry — see the module docstring for
    why a tenant's cached value depends on the platform row.
    """
    store = _active_store
    if store is None:
        return 0
    if tenant_id is None:
        return store.delete_prefix(setting_key_prefix(domain, key))
    return store.delete_prefix(setting_cache_key(domain, key, tenant_id=tenant_id))


__all__ = [
    "MISS",
    "active_settings_cache",
    "cached",
    "install_settings_cache",
    "invalidate",
    "setting_cache_key",
    "setting_key_prefix",
    "store_resolved",
]
