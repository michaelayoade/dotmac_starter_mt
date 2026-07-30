"""Tenant-aware rate limiting middleware (store contract + bounded memory).

Control-plane security Task 5 hardened two unbounded growth vectors:

1. **Unbounded key cardinality** — the key used the RAW request path, so a
   scanner walking random URLs minted a new bucket per URL. The key now
   uses the MATCHED ROUTE TEMPLATE (`/parties/{party_id}`, resolved against
   `app.routes` the same way Starlette routing does) when one matches, and
   an unmatched path collapses into one of a fixed number of hash buckets.
2. **Unbounded memory** — `MemoryStore` is LRU-capped (`rate_limit_max_keys`
   knob, default 10_000): the oldest-touched key is evicted at the cap, so
   worst-case memory is bounded regardless of client behavior.

The store is a CONTRACT (`RateLimitStore` protocol), not a Redis ship
(contracts-not-implementations): a multi-process deployment swaps
`MemoryStore` for a Redis-backed implementation with the same `hit()`
shape and the same key model — the `RATE_LIMIT_REDIS_URL` settings knob is
reserved for that swap; no redis dependency ships with the starter. See
docs/SECURITY.md.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, deque
from typing import Protocol

from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import envelope

# Unmatched paths (no route template) collapse into this many hash buckets —
# scanners can't mint per-URL keys, real 404 traffic still gets limited.
_UNMATCHED_BUCKETS = 64


class RateLimitStore(Protocol):
    """The swap seam for a shared (e.g. Redis) backend."""

    def hit(
        self, key: str, now: float, *, window_seconds: int, limit: int
    ) -> tuple[int, int]:
        """Record one hit for `key` at `now`. Returns `(count, retry_after)`:
        `count` is the hits inside the current window INCLUDING this one when
        admitted; a `count` > `limit` means the hit was rejected (not
        recorded) and `retry_after` is the whole-seconds wait suggestion."""
        ...


class MemoryStore:
    """Process-local sliding-window store, LRU-bounded at `max_keys`."""

    def __init__(self, *, max_keys: int = 10_000) -> None:
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(
        self, key: str, now: float, *, window_seconds: int, limit: int
    ) -> tuple[int, int]:
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            return len(hits) + 1, retry_after
        hits.append(now)
        self._evict()
        return len(hits), 0

    def _evict(self) -> None:
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        requests: int = 120,
        window_seconds: int = 60,
        store: RateLimitStore | None = None,
        max_keys: int = 10_000,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.requests = requests
        self.window_seconds = window_seconds
        self.store: RateLimitStore = (
            store if store is not None else MemoryStore(max_keys=max_keys)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if not self.enabled or request.url.path == "/health":
            await self.app(scope, receive, send)
            return

        key = _rate_limit_key(request)
        count, retry_after = self.store.hit(
            key,
            time.monotonic(),
            window_seconds=self.window_seconds,
            limit=self.requests,
        )
        if retry_after:
            response = JSONResponse(
                status_code=429,
                content=envelope("rate_limited", "Rate limit exceeded"),
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _path_bucket(request: Request) -> str:
    """Route TEMPLATE when one matches (bounded by the route table), else a
    fixed hash bucket (bounded by `_UNMATCHED_BUCKETS`) — never the raw
    path."""
    app = request.scope.get("app")
    if app is not None:
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                return getattr(route, "path", request.url.path)
    digest = hashlib.sha256(request.url.path.encode()).digest()
    return f"unmatched:{digest[0] % _UNMATCHED_BUCKETS}"


def _rate_limit_key(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    tenant_key = str(tenant.id) if tenant is not None else "platform"
    client_ip = request.client.host if request.client else "unknown"
    return f"rate_limit:{tenant_key}:{client_ip}:{_path_bucket(request)}"
