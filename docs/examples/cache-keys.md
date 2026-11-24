# Tenant-aware cache keys

The [kernel cache owner](../../packages/dotmac-kernel/src/dotmac_kernel/cache.py)
builds keys. Use the public API rather than assembling strings in your product.
These examples run against the actual package in hosted unit CI; they do not
connect to Redis or a database and do not prove database isolation.

    >>> from uuid import UUID
    >>> from dotmac_kernel.cache import PlatformScope, TenantScope, cache_key
    >>> tenant = TenantScope(UUID("00000000-0000-0000-0000-000000000001"))
    >>> cache_key("flags", "example.enabled", scope=tenant)
    'flags:example.enabled:t=00000000-0000-0000-0000-000000000001'
    >>> cache_key("flags", "example.enabled", scope=PlatformScope())
    'flags:example.enabled:platform'
    >>> cache_key("flags", scope=tenant, version=2).endswith(":v=2")
    True

Parts cannot inject the key separator:

    >>> cache_key("flags:other-tenant", scope=tenant)
    Traceback (most recent call last):
        ...
    ValueError: cache key part 'flags:other-tenant' contains ':' — a part that can inject a separator can forge another key's shape

See [documentation maintenance](../guides/documentation.md) for example-test scope.
