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

**Anonymous routes get a second, stricter limit** (`AnonymousRateLimitPolicy`).
The product declares which route templates accept anonymous callers; each is
counted per `(tenant, client address, route)` against its own, lower budget, in
addition to the global one. Two properties are the point:

* *The client address is the trusted one.* It is read from the request-evidence
  context, which believes `X-Forwarded-For` only from a peer inside the declared
  trusted-proxy set (`TrustedProxyPolicy`). Without evidence the transport peer
  is used. A forwarded header from an untrusted peer can therefore neither evade
  the limit by rotating nor frame another address.
* *The store is shared, and production insists on it.* `PostgresRateLimitStore`
  counts in the kernel `rate_limit_buckets` table (migration 0031), so N workers
  enforce one limit. Production REFUSES a store that does not declare itself
  shared (the `MemoryStore`), at construction, with a message naming the fix.
  The store failing closes the route (503), it never silently stops limiting.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Protocol

from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.dialects import postgresql, sqlite
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from dotmac_kernel.config import ANONYMOUS_RATE_LIMIT_STORES, Settings
from dotmac_kernel.errors import envelope
from dotmac_kernel.models_platform import RateLimitBucket
from dotmac_kernel.request_evidence import UNSET_EVIDENCE, current_evidence

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

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
    """Process-local sliding-window store, LRU-bounded at `max_keys`.

    `shared = False`: the count is per worker process. Fine for the global
    limiter's blunt abuse bound and for development; refused in production for
    the anonymous-route limiter, whose budget must hold per client.
    """

    shared: ClassVar[bool] = False

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


class AnonymousRateLimitStoreError(RuntimeError):
    """The anonymous-route limiter was given a store production cannot trust."""


_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"}
)


@dataclass(frozen=True, slots=True)
class AnonymousRouteRule:
    """One declared anonymous route: a route TEMPLATE, optionally one method."""

    template: str
    method: str | None = None

    def __post_init__(self) -> None:
        if not self.template.startswith("/"):
            raise ValueError(
                f"anonymous rate-limit route {self.template!r} must be a route "
                "template starting with '/'"
            )
        if self.method is not None and self.method not in _HTTP_METHODS:
            raise ValueError(
                f"anonymous rate-limit route {self.template!r} names an unknown "
                f"HTTP method {self.method!r}"
            )

    @property
    def label(self) -> str:
        return f"{self.method or '*'} {self.template}"

    def matches(self, method: str, template: str) -> bool:
        return template == self.template and self.method in (None, method.upper())


@dataclass(frozen=True, slots=True)
class AnonymousRateLimitPolicy:
    """Which routes are anonymous, and how many hits each client gets on each.

    Policy VALUES come from `Settings` (`anonymous_rate_limit_*`); nothing here
    is a default a product can inherit silently, and an empty rule set is not a
    policy -- build none instead.
    """

    rules: tuple[AnonymousRouteRule, ...]
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.rules:
            raise ValueError("an anonymous rate-limit policy needs at least one route")
        if self.requests < 1 or self.window_seconds < 1:
            raise ValueError(
                "anonymous rate-limit requests and window_seconds must be positive"
            )

    @classmethod
    def from_declaration(
        cls, routes: str, *, requests: int, window_seconds: int
    ) -> AnonymousRateLimitPolicy | None:
        """Parse `"POST /auth/login, /forms/{token}"`; `None` when nothing declared."""
        rules: list[AnonymousRouteRule] = []
        for entry in (part.strip() for part in routes.split(",")):
            if not entry:
                continue
            tokens = entry.split()
            if len(tokens) == 1:
                rules.append(AnonymousRouteRule(template=tokens[0]))
            elif len(tokens) == 2:
                rules.append(
                    AnonymousRouteRule(template=tokens[1], method=tokens[0].upper())
                )
            else:
                raise ValueError(
                    f"anonymous rate-limit route {entry!r} must be "
                    "'<template>' or '<METHOD> <template>'"
                )
        if not rules:
            return None
        return cls(rules=tuple(rules), requests=requests, window_seconds=window_seconds)

    def rule_for(self, method: str, template: str) -> AnonymousRouteRule | None:
        for rule in self.rules:
            if rule.matches(method, template):
                return rule
        return None


SessionProvider = Callable[[], "AbstractContextManager[Session]"]


def _platform_session() -> AbstractContextManager[Session]:
    # Resolved lazily so importing this module never needs a database URL.
    from dotmac_kernel.session_runtime import resolve_database_runtime

    return resolve_database_runtime().platform_session()


class PostgresRateLimitStore:
    """Shared fixed-window store in the kernel `rate_limit_buckets` table.

    One atomic `INSERT .. ON CONFLICT DO UPDATE .. RETURNING` per hit, so
    concurrent workers cannot lose an increment. Fixed windows allow up to twice
    the budget across a boundary; that is the price of one statement and no
    locks, and the budget here is a brute-force bound, not a billing meter. A
    rejected hit is still counted, so a client that keeps hammering stays
    rejected for the rest of its window (the window does not slide).

    Only the SHA-256 of the key is stored. Expired rows are pruned
    opportunistically every `prune_every` hits per process. Any database error
    propagates: the middleware turns it into a refusal rather than an unlimited
    route. Written for PostgreSQL; SQLite is accepted so unit tests can run the
    identical statement.
    """

    shared: ClassVar[bool] = True

    def __init__(
        self,
        *,
        session_provider: SessionProvider | None = None,
        clock: Callable[[], float] = time.time,
        prune_every: int = 256,
    ) -> None:
        if prune_every < 1:
            raise ValueError("prune_every must be positive")
        self._session_provider = session_provider or _platform_session
        self._clock = clock
        self._prune_every = prune_every
        self._calls = 0

    def hit(
        self, key: str, now: float, *, window_seconds: int, limit: int
    ) -> tuple[int, int]:
        # `now` is the caller's monotonic clock, meaningless to another process.
        # A shared window needs wall time, which every worker agrees on to NTP.
        wall = self._clock()
        start = int(wall // window_seconds) * window_seconds
        window_start = datetime.fromtimestamp(start, UTC)
        expires_at = window_start + timedelta(seconds=2 * window_seconds)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        with self._session_provider() as db:
            dialect = db.get_bind().dialect.name
            if dialect == "postgresql":
                insert = postgresql.insert
            elif dialect == "sqlite":
                insert = sqlite.insert
            else:
                raise RuntimeError(
                    f"PostgresRateLimitStore does not support dialect {dialect!r}"
                )
            statement = insert(RateLimitBucket).values(
                key_hash=key_hash,
                window_start=window_start,
                hit_count=1,
                expires_at=expires_at,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["key_hash", "window_start"],
                set_={"hit_count": RateLimitBucket.hit_count + 1},
            ).returning(RateLimitBucket.hit_count)
            count = int(db.execute(statement).scalar_one())
            self._calls += 1
            if self._calls % self._prune_every == 0:
                db.execute(
                    delete(RateLimitBucket).where(
                        RateLimitBucket.expires_at < datetime.fromtimestamp(wall, UTC)
                    )
                )
        if count <= limit:
            return count, 0
        return count, max(1, int(start + window_seconds - wall))


def build_anonymous_rate_limit(
    s: Settings,
) -> tuple[AnonymousRateLimitPolicy, RateLimitStore] | None:
    """Policy and store from `Settings`, or `None` when no route is declared.

    Refuses -- raising `AnonymousRateLimitStoreError` -- an in-memory store in
    production, and any malformed value, at the moment the application is built
    rather than on the first anonymous request.
    """
    from dotmac_kernel.config import anonymous_rate_limit_errors

    errors = anonymous_rate_limit_errors(s)
    if errors:
        raise AnonymousRateLimitStoreError(
            "Anonymous rate-limit configuration error: " + "; ".join(errors)
        )
    policy = AnonymousRateLimitPolicy.from_declaration(
        s.anonymous_rate_limit_routes,
        requests=s.anonymous_rate_limit_requests,
        window_seconds=s.anonymous_rate_limit_window_seconds,
    )
    if policy is None:
        return None
    kind = s.anonymous_rate_limit_store.strip().lower()
    assert kind in ANONYMOUS_RATE_LIMIT_STORES
    store: RateLimitStore = (
        PostgresRateLimitStore()
        if kind == "postgres"
        else MemoryStore(max_keys=s.rate_limit_max_keys)
    )
    return policy, store


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
        anonymous_policy: AnonymousRateLimitPolicy | None = None,
        anonymous_store: RateLimitStore | None = None,
        production: bool = False,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.requests = requests
        self.window_seconds = window_seconds
        self.store: RateLimitStore = (
            store if store is not None else MemoryStore(max_keys=max_keys)
        )
        self.anonymous_policy = anonymous_policy
        self.anonymous_store: RateLimitStore | None = None
        if anonymous_policy is not None:
            candidate = (
                anonymous_store
                if anonymous_store is not None
                else (None if production else MemoryStore(max_keys=max_keys))
            )
            if candidate is None or (
                production and getattr(candidate, "shared", False) is not True
            ):
                raise AnonymousRateLimitStoreError(
                    "production refuses an in-memory (or undeclared) store for "
                    "the anonymous-route rate limit: the count would be per "
                    "worker process and reset on every restart. Pass a store "
                    "that declares shared = True, such as PostgresRateLimitStore "
                    "(kernel migration 0031_rate_limit_buckets)"
                )
            self.anonymous_store = candidate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.url.path == "/health":
            await self.app(scope, receive, send)
            return

        template = _path_bucket(request) if self._needs_template() else None
        if (
            self.anonymous_policy is not None
            and self.anonymous_store is not None
            and template is not None
        ):
            rule = self.anonymous_policy.rule_for(request.method, template)
            if rule is not None:
                refusal = await self._hit_anonymous(request, rule)
                if refusal is not None:
                    await refusal(scope, receive, send)
                    return

        if not self.enabled:
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

    def _needs_template(self) -> bool:
        return self.anonymous_policy is not None

    async def _hit_anonymous(
        self, request: Request, rule: AnonymousRouteRule
    ) -> JSONResponse | None:
        """Count one anonymous hit; a response to send instead when refused."""
        policy, store = self.anonymous_policy, self.anonymous_store
        assert policy is not None and store is not None
        key = _anonymous_rate_limit_key(request, rule)
        try:
            if getattr(store, "shared", False):
                # A shared store is a database round trip: keep it off the loop.
                count, retry_after = await asyncio.to_thread(
                    store.hit,
                    key,
                    time.monotonic(),
                    window_seconds=policy.window_seconds,
                    limit=policy.requests,
                )
            else:
                count, retry_after = store.hit(
                    key,
                    time.monotonic(),
                    window_seconds=policy.window_seconds,
                    limit=policy.requests,
                )
        except Exception:
            # Fail CLOSED. An anonymous route whose limiter cannot count is an
            # unthrottled credential-guessing surface; refusing is the safe side.
            logger.exception("anonymous rate-limit store failed; refusing request")
            return JSONResponse(
                status_code=503,
                content=envelope(
                    "rate_limit_unavailable", "Rate limiting is unavailable"
                ),
                headers={"Retry-After": "5"},
            )
        if retry_after:
            return JSONResponse(
                status_code=429,
                content=envelope("rate_limited", "Rate limit exceeded"),
                headers={"Retry-After": str(retry_after)},
            )
        return None


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


def trusted_client_address(request: Request) -> str:
    """The client address the request-evidence context resolved, else the peer.

    The evidence context believes `X-Forwarded-For` / `X-Real-IP` only from a
    peer inside the declared trusted-proxy set, so this is the "existing
    trusted-proxy client IP". When the assembly declares no request evidence the
    transport peer is the only address that can be believed, and is used.
    """
    evidence = current_evidence()
    if evidence is not UNSET_EVIDENCE and evidence.client_address:
        return evidence.client_address
    return request.client.host if request.client else "unknown"


def _anonymous_rate_limit_key(request: Request, rule: AnonymousRouteRule) -> str:
    tenant = getattr(request.state, "tenant", None)
    tenant_key = str(tenant.id) if tenant is not None else "platform"
    return (
        f"anonymous_rate_limit:{tenant_key}:{trusted_client_address(request)}:"
        f"{rule.label}"
    )


__all__ = [
    "AnonymousRateLimitPolicy",
    "AnonymousRateLimitStoreError",
    "AnonymousRouteRule",
    "MemoryStore",
    "PostgresRateLimitStore",
    "RateLimitMiddleware",
    "RateLimitStore",
    "build_anonymous_rate_limit",
    "trusted_client_address",
]
