"""The anonymous-route rate limit: trusted client address, shared store, prod gate.

Addresses are RFC 5737 documentation ranges. The Postgres store's statement is
run on SQLite here (same SQLAlchemy upsert); the PostgreSQL grants and the
migration itself are covered by the hosted migration/RLS suites, not here.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.config import (
    Settings,
    anonymous_rate_limit_errors,
    validate_settings,
)
from dotmac_kernel.middleware.rate_limit import (
    AnonymousRateLimitConfigurationError,
    AnonymousRateLimitPolicy,
    AnonymousRateLimitStoreError,
    DedicatedLimiterSessions,
    MemoryStore,
    PostgresRateLimitStore,
    RateLimitMiddleware,
    anonymous_rate_limit_route_errors,
    build_anonymous_rate_limit,
    trusted_client_address,
)
from dotmac_kernel.models_platform import RateLimitBucket
from dotmac_kernel.request_evidence import (
    RequestEvidenceContextV1,
    TrustedProxyPolicy,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

KEY_SECRET = (
    "k2-test-secret-" + "x" * 32
)  # documentation value, not a deployment secret
UNTRUSTED_PEER = "198.51.100.7"
TRUSTED_PROXY = "192.0.2.10"
PROXIES = TrustedProxyPolicy.from_declaration("192.0.2.0/24")


class _CapturingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str] = []

    def hit(self, key, now, *, window_seconds, limit):  # type: ignore[no-untyped-def]
        self.keys.append(key)
        return super().hit(key, now, window_seconds=window_seconds, limit=limit)


def _app(
    *,
    store: MemoryStore | None = None,
    requests: int = 2,
    routes: str = "POST /auth/login",
    global_enabled: bool = False,
    evidence: bool = True,
) -> FastAPI:
    app = FastAPI()

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/auth/login")
    def login_page() -> dict[str, str]:
        return {"page": "yes"}

    @app.get("/other")
    def other() -> dict[str, str]:
        return {"other": "yes"}

    policy = AnonymousRateLimitPolicy.from_declaration(
        routes, requests=requests, window_seconds=60
    )
    app.add_middleware(
        RateLimitMiddleware,
        enabled=global_enabled,
        anonymous_policy=policy,
        anonymous_store=store or MemoryStore(),
    )
    if evidence:
        app.add_middleware(RequestEvidenceContextV1, trusted_proxies=PROXIES)
    return app


def _post(client: TestClient, **headers: str) -> int:
    return client.post("/auth/login", headers=headers).status_code


# ── policy declaration ─────────────────────────────────────────────────────


def test_declaration_parses_methods_and_templates() -> None:
    policy = AnonymousRateLimitPolicy.from_declaration(
        "post /auth/login, /forms/{token}", requests=3, window_seconds=30
    )
    assert policy is not None
    assert policy.rule_for("POST", "/auth/login") is not None
    assert policy.rule_for("GET", "/auth/login") is None
    assert policy.rule_for("DELETE", "/forms/{token}") is not None
    assert (policy.requests, policy.window_seconds) == (3, 30)


def test_empty_declaration_is_no_policy() -> None:
    assert (
        AnonymousRateLimitPolicy.from_declaration(" , ", requests=1, window_seconds=1)
        is None
    )


@pytest.mark.parametrize(
    "declaration",
    ["auth/login", "FETCH /x", "POST /a /b"],
)
def test_malformed_declarations_are_refused(declaration: str) -> None:
    with pytest.raises(ValueError):
        AnonymousRateLimitPolicy.from_declaration(
            declaration, requests=1, window_seconds=1
        )


def test_non_positive_budgets_are_refused() -> None:
    with pytest.raises(ValueError):
        AnonymousRateLimitPolicy.from_declaration("/x", requests=0, window_seconds=1)


# ── the limit is enforced ──────────────────────────────────────────────────


def test_limit_is_enforced_per_route_and_method() -> None:
    client = TestClient(_app(), client=(UNTRUSTED_PEER, 5000))

    assert [_post(client) for _ in range(3)] == [200, 200, 429]
    refused = client.post("/auth/login")
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.json()["code"] == "rate_limited"
    # A different method on the same template, and a different route, are not
    # anonymous-declared and keep their normal (here: disabled) budget.
    assert client.get("/auth/login").status_code == 200
    assert client.get("/other").status_code == 200


def test_clients_have_independent_budgets() -> None:
    app = _app()
    first = TestClient(app, client=(UNTRUSTED_PEER, 5000))
    second = TestClient(app, client=("198.51.100.8", 5000))

    assert [_post(first) for _ in range(3)] == [200, 200, 429]
    assert _post(second) == 200


def test_anonymous_limit_is_stricter_than_the_global_one() -> None:
    app = FastAPI()

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        return {}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        requests=100,
        window_seconds=60,
        anonymous_policy=AnonymousRateLimitPolicy.from_declaration(
            "/auth/login", requests=1, window_seconds=60
        ),
        anonymous_store=MemoryStore(),
    )
    client = TestClient(app)
    assert [client.post("/auth/login").status_code for _ in range(2)] == [200, 429]


def test_no_policy_leaves_the_global_limiter_untouched() -> None:
    app = FastAPI()

    @app.get("/x")
    def x() -> dict[str, str]:
        return {}

    app.add_middleware(RateLimitMiddleware, enabled=True, requests=1)
    client = TestClient(app)
    assert [client.get("/x").status_code for _ in range(2)] == [200, 429]


# ── keyed on the trusted-proxy client address ──────────────────────────────


def test_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored() -> None:
    store = _CapturingStore()
    client = TestClient(_app(store=store), client=(UNTRUSTED_PEER, 5000))

    # Rotating the forwarded header does not mint fresh budgets...
    codes = [
        _post(client, **{"X-Forwarded-For": f"203.0.113.{n}"}) for n in range(1, 5)
    ]
    assert codes == [200, 200, 429, 429]
    # ...and every key was derived from the transport peer, never the header.
    assert all(UNTRUSTED_PEER in key for key in store.keys)
    assert not any("203.0.113." in key for key in store.keys)


def test_x_real_ip_from_an_untrusted_peer_is_ignored_too() -> None:
    store = _CapturingStore()
    client = TestClient(_app(store=store), client=(UNTRUSTED_PEER, 5000))

    _post(client, **{"X-Real-IP": "203.0.113.99"})
    assert UNTRUSTED_PEER in store.keys[0]
    assert "203.0.113.99" not in store.keys[0]


def test_a_trusted_proxy_forwarded_address_is_the_key() -> None:
    store = _CapturingStore()
    client = TestClient(_app(store=store), client=(TRUSTED_PROXY, 5000))

    assert [_post(client, **{"X-Forwarded-For": "203.0.113.1"}) for _ in range(3)] == [
        200,
        200,
        429,
    ]
    # A different real client behind the same proxy has its own budget.
    assert _post(client, **{"X-Forwarded-For": "203.0.113.2"}) == 200
    assert "203.0.113.1" in store.keys[0]
    assert TRUSTED_PROXY not in store.keys[0]


def test_without_request_evidence_the_transport_peer_is_used() -> None:
    store = _CapturingStore()
    client = TestClient(
        _app(store=store, evidence=False), client=(UNTRUSTED_PEER, 5000)
    )

    _post(client, **{"X-Forwarded-For": "203.0.113.5"})
    assert UNTRUSTED_PEER in store.keys[0]
    assert "203.0.113.5" not in store.keys[0]


# ── fail closed when the store fails ───────────────────────────────────────


class _BrokenSharedStore:
    shared = True

    def hit(self, key, now, *, window_seconds, limit):  # type: ignore[no-untyped-def]
        raise RuntimeError("database unavailable: secret-dsn-must-not-leak")


def test_a_failing_store_refuses_the_request_with_503() -> None:
    app = FastAPI()
    reached: list[bool] = []

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        reached.append(True)
        return {}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=False,
        anonymous_policy=AnonymousRateLimitPolicy.from_declaration(
            "/auth/login", requests=5, window_seconds=60
        ),
        anonymous_store=_BrokenSharedStore(),
    )
    response = TestClient(app).post("/auth/login")

    assert response.status_code == 503
    assert response.json()["code"] == "rate_limit_unavailable"
    assert "secret-dsn" not in response.text
    assert reached == []


# ── production refuses an in-memory store ──────────────────────────────────

_POLICY = AnonymousRateLimitPolicy.from_declaration(
    "/auth/login", requests=5, window_seconds=60
)


def _noop(scope, receive, send):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def test_production_refuses_an_explicit_memory_store() -> None:
    with pytest.raises(AnonymousRateLimitStoreError, match="in-memory"):
        RateLimitMiddleware(
            _noop,
            anonymous_policy=_POLICY,
            anonymous_store=MemoryStore(),
            production=True,
        )


def test_production_refuses_a_missing_store_rather_than_defaulting_to_memory() -> None:
    with pytest.raises(AnonymousRateLimitStoreError):
        RateLimitMiddleware(_noop, anonymous_policy=_POLICY, production=True)


def test_production_refuses_a_store_that_does_not_declare_itself_shared() -> None:
    class Undeclared:
        def hit(self, key, now, *, window_seconds, limit):  # type: ignore[no-untyped-def]
            return 1, 0

    with pytest.raises(AnonymousRateLimitStoreError):
        RateLimitMiddleware(
            _noop,
            anonymous_policy=_POLICY,
            anonymous_store=Undeclared(),
            production=True,
        )


def test_production_accepts_the_postgres_store_and_development_defaults_to_memory() -> (
    None
):
    RateLimitMiddleware(
        _noop,
        anonymous_policy=_POLICY,
        anonymous_store=PostgresRateLimitStore(),
        production=True,
    )
    dev = RateLimitMiddleware(_noop, anonymous_policy=_POLICY)
    assert isinstance(dev.anonymous_store, MemoryStore)


def _settings(**overrides: object) -> Settings:
    return Settings(
        **{
            "environment": "production",
            "anonymous_rate_limit_routes": "POST /auth/login",
            "anonymous_rate_limit_key_secret": KEY_SECRET,
            **overrides,
        }
    )


def test_settings_refuse_the_memory_store_in_production_with_a_clear_message() -> None:
    errors = anonymous_rate_limit_errors(_settings(anonymous_rate_limit_store="memory"))

    assert len(errors) == 1
    assert "ANONYMOUS_RATE_LIMIT_STORE=memory is refused in production" in errors[0]
    assert "0031_rate_limit_buckets" in errors[0]
    assert any(
        "ANONYMOUS_RATE_LIMIT_STORE" in e
        for e in validate_settings(_settings(anonymous_rate_limit_store="memory"))
    )


def test_settings_accept_memory_outside_production_and_postgres_in_production() -> None:
    assert (
        anonymous_rate_limit_errors(
            _settings(environment="dev", anonymous_rate_limit_store="memory")
        )
        == []
    )
    assert anonymous_rate_limit_errors(_settings()) == []


def test_undeclared_routes_need_no_store_and_raise_no_error() -> None:
    quiet = _settings(
        anonymous_rate_limit_routes="", anonymous_rate_limit_store="memory"
    )
    assert anonymous_rate_limit_errors(quiet) == []
    assert build_anonymous_rate_limit(quiet) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"anonymous_rate_limit_store": "redis"},
        {"anonymous_rate_limit_requests": 0},
        {"anonymous_rate_limit_window_seconds": 0},
    ],
)
def test_malformed_values_are_configuration_errors(
    overrides: dict[str, object],
) -> None:
    assert anonymous_rate_limit_errors(_settings(**overrides))


def test_builder_refuses_memory_in_production_and_builds_postgres() -> None:
    with pytest.raises(AnonymousRateLimitStoreError, match="refused in production"):
        build_anonymous_rate_limit(_settings(anonymous_rate_limit_store="memory"))

    built = build_anonymous_rate_limit(_settings())
    assert built is not None
    policy, store = built
    assert isinstance(store, PostgresRateLimitStore)
    assert policy.rule_for("POST", "/auth/login") is not None


def test_create_app_fails_closed_in_production_with_the_memory_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dotmac_kernel import app_factory
    from dotmac_kernel.api_documentation import api_documentation_policy

    monkeypatch.setattr(app_factory.settings, "environment", "production")
    monkeypatch.setattr(
        app_factory.settings, "anonymous_rate_limit_routes", "POST /auth/login"
    )
    monkeypatch.setattr(app_factory.settings, "anonymous_rate_limit_store", "memory")
    spec = ProductAssemblySpec(
        api_documentation=api_documentation_policy("development"), name="k2-probe"
    )

    with pytest.raises(AnonymousRateLimitStoreError, match="refused in production"):
        create_app(spec)


def test_create_app_is_unaffected_when_no_route_is_declared() -> None:
    from dotmac_kernel.api_documentation import api_documentation_policy

    app = create_app(
        ProductAssemblySpec(
            api_documentation=api_documentation_policy("development"), name="k2-quiet"
        )
    )
    assert app is not None


# ── the shared Postgres store (statement exercised on SQLite) ──────────────


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _store(db: Session, clock: _Clock, **kw: object) -> PostgresRateLimitStore:
    @contextmanager
    def provider() -> Iterator[Session]:
        yield db
        db.flush()

    return PostgresRateLimitStore(
        key_secret=KEY_SECRET, session_provider=provider, clock=clock, **kw
    )  # type: ignore[arg-type]


def test_postgres_store_counts_rejects_and_reports_retry_after(db: Session) -> None:
    clock = _Clock(1_000_000_010.0)  # 10s into a 60s window
    store = _store(db, clock)

    results = [store.hit("k", 0.0, window_seconds=60, limit=2) for _ in range(4)]

    assert [count for count, _ in results] == [1, 2, 3, 4]
    assert [retry for _, retry in results[:2]] == [0, 0]
    assert all(retry > 0 for _, retry in results[2:])
    window_end = (int(clock.now // 60) + 1) * 60
    assert results[2][1] == int(window_end - clock.now)


def test_postgres_store_resets_in_the_next_window_and_isolates_keys(
    db: Session,
) -> None:
    clock = _Clock(1_000_000_010.0)
    store = _store(db, clock)
    for _ in range(3):
        store.hit("k", 0.0, window_seconds=60, limit=2)

    assert store.hit("other", 0.0, window_seconds=60, limit=2) == (1, 0)
    clock.now += 60
    assert store.hit("k", 0.0, window_seconds=60, limit=2) == (1, 0)


def test_postgres_store_persists_only_a_hash_of_the_key(db: Session) -> None:
    store = _store(db, _Clock(1_000_000_010.0))
    key = f"anonymous_rate_limit:platform:{UNTRUSTED_PEER}:POST /auth/login"
    store.hit(key, 0.0, window_seconds=60, limit=5)

    rows = db.scalars(select(RateLimitBucket)).all()
    assert len(rows) == 1
    assert len(rows[0].key_hash) == 64
    assert UNTRUSTED_PEER not in rows[0].key_hash
    # Keyed: the stored value is the HMAC, and NOT the unsalted SHA-256 that an
    # offline guess over the address space could reproduce.
    expected = hmac.new(KEY_SECRET.encode(), key.encode(), hashlib.sha256)
    assert rows[0].key_hash == expected.hexdigest()
    assert rows[0].key_hash != hashlib.sha256(key.encode()).hexdigest()
    assert rows[0].hit_count == 1
    assert rows[0].expires_at > rows[0].window_start


def test_postgres_store_prunes_expired_windows(db: Session) -> None:
    clock = _Clock(1_000_000_010.0)
    store = _store(db, clock, prune_every=2)
    store.hit("old", 0.0, window_seconds=60, limit=5)
    clock.now += 600  # far past old's expiry
    store.hit("new", 0.0, window_seconds=60, limit=5)  # call 2 triggers the prune

    remaining = db.scalars(select(RateLimitBucket)).all()
    assert len(remaining) == 1
    expected = datetime.fromtimestamp(int(clock.now // 60) * 60, UTC)
    # SQLite hands back naive datetimes; PostgreSQL returns them aware.
    assert remaining[0].window_start.replace(tzinfo=None) == expected.replace(
        tzinfo=None
    )


def test_two_store_instances_share_one_count(db: Session) -> None:
    """The point of the shared store: two workers, one limit."""
    clock = _Clock(1_000_000_010.0)
    worker_a, worker_b = _store(db, clock), _store(db, clock)

    assert worker_a.hit("k", 0.0, window_seconds=60, limit=2) == (1, 0)
    assert worker_b.hit("k", 0.0, window_seconds=60, limit=2) == (2, 0)
    assert worker_a.hit("k", 0.0, window_seconds=60, limit=2)[1] > 0


def test_postgres_store_refuses_an_unsupported_dialect() -> None:
    class _Bind:
        class dialect:
            name = "mysql"

    class _Db:
        def get_bind(self) -> _Bind:
            return _Bind()

    @contextmanager
    def provider() -> Iterator[_Db]:
        yield _Db()

    store = PostgresRateLimitStore(key_secret=KEY_SECRET, session_provider=provider)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="does not support dialect"):
        store.hit("k", 0.0, window_seconds=60, limit=1)


def test_postgres_store_refuses_an_empty_key_secret() -> None:
    with pytest.raises(ValueError, match="key secret"):
        PostgresRateLimitStore(key_secret="")


def test_different_secrets_store_different_hashes(db: Session) -> None:
    clock = _Clock(1_000_000_010.0)

    @contextmanager
    def provider() -> Iterator[Session]:
        yield db
        db.flush()

    for secret in ("a" * 32, "b" * 32):
        PostgresRateLimitStore(
            key_secret=secret, session_provider=provider, clock=clock
        ).hit("k", 0.0, window_seconds=60, limit=5)
    hashes = {row.key_hash for row in db.scalars(select(RateLimitBucket)).all()}
    assert len(hashes) == 2


# ── secret configuration (by name; values are never printed) ───────────────


def test_production_requires_a_real_key_secret_for_the_postgres_store() -> None:
    errors = anonymous_rate_limit_errors(
        _settings(anonymous_rate_limit_key_secret="dev-insecure-change-me")
    )
    assert len(errors) == 1
    assert "ANONYMOUS_RATE_LIMIT_KEY_SECRET must be set" in errors[0]
    short = anonymous_rate_limit_errors(
        _settings(anonymous_rate_limit_key_secret="short")
    )
    assert "at least 32 bytes" in short[0]
    assert "short" not in short[0].replace("at least", "")
    reused = anonymous_rate_limit_errors(
        _settings(jwt_secret=KEY_SECRET, anonymous_rate_limit_key_secret=KEY_SECRET)
    )
    assert "distinct" in reused[0]
    assert KEY_SECRET not in " ".join(reused)


def test_development_accepts_the_default_key_secret() -> None:
    assert (
        anonymous_rate_limit_errors(
            _settings(
                environment="dev",
                anonymous_rate_limit_key_secret="dev-insecure-change-me",
            )
        )
        == []
    )


# ── IPv6 clients are limited per /64 ───────────────────────────────────────


def _scope_request(client_host: str) -> object:
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": (client_host, 1),
        }
    )


@pytest.mark.parametrize(
    "address, subject",
    [
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:1111:2222:3333:4444", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("198.51.100.9", "198.51.100.9"),
        ("unknown", "unknown"),
    ],
)
def test_the_rate_limit_subject_is_an_ipv4_or_an_ipv6_slash_64(
    address: str, subject: str
) -> None:
    assert trusted_client_address(_scope_request(address)) == subject  # type: ignore[arg-type]


def test_one_ipv6_slash_64_shares_one_budget() -> None:
    store = _CapturingStore()
    client = TestClient(_app(store=store), client=(TRUSTED_PROXY, 5000))
    codes = [
        _post(client, **{"X-Forwarded-For": f"2001:db8:1:2::{n:x}"})
        for n in range(1, 5)
    ]
    assert codes == [200, 200, 429, 429]
    assert all("2001:db8:1:2::/64" in key for key in store.keys)


def test_spoofed_leftmost_forwarded_entries_do_not_mint_budgets() -> None:
    store = _CapturingStore()
    client = TestClient(_app(store=store), client=(TRUSTED_PROXY, 5000))
    # The proxy appended the real client (198.51.100.20); the caller rotates the
    # forged leftmost entry on every request.
    codes = [
        _post(client, **{"X-Forwarded-For": f"203.0.113.{n}, 198.51.100.20"})
        for n in range(1, 5)
    ]
    assert codes == [200, 200, 429, 429]
    assert all("198.51.100.20" in key for key in store.keys)
    assert not any("203.0.113." in key for key in store.keys)


# ── a store that is over budget stops costing database round trips ─────────


class _CountingSharedStore(MemoryStore):
    shared = True

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def hit(self, key, now, *, window_seconds, limit):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().hit(key, now, window_seconds=window_seconds, limit=limit)


def _prod_app(store: _CountingSharedStore, **kw: object) -> FastAPI:
    app = FastAPI()

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        return {}

    app.add_middleware(
        RateLimitMiddleware,
        production=True,
        anonymous_policy=AnonymousRateLimitPolicy.from_declaration(
            "/auth/login", requests=2, window_seconds=60
        ),
        anonymous_store=store,
        **kw,  # type: ignore[arg-type]
    )
    return app


def test_a_blocked_key_is_refused_without_touching_the_store() -> None:
    store = _CountingSharedStore()
    client = TestClient(_prod_app(store, enabled=False))

    assert [client.post("/auth/login").status_code for _ in range(3)] == [200, 200, 429]
    calls_when_blocked = store.calls
    for _ in range(20):
        response = client.post("/auth/login")
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) >= 1
    assert store.calls == calls_when_blocked


def test_blocking_is_per_key() -> None:
    store = _CountingSharedStore()
    app = _prod_app(store, enabled=False)
    TestClient(app, client=("198.51.100.1", 1)).post("/auth/login")
    blocked = TestClient(app, client=("198.51.100.2", 1))
    assert [blocked.post("/auth/login").status_code for _ in range(3)] == [
        200,
        200,
        429,
    ]
    assert (
        TestClient(app, client=("198.51.100.1", 1)).post("/auth/login").status_code
        == 200
    )


def test_the_global_limiter_runs_before_the_anonymous_store() -> None:
    store = _CountingSharedStore()
    client = TestClient(_prod_app(store, enabled=True, requests=1, window_seconds=60))

    assert client.post("/auth/login").status_code == 200
    assert store.calls == 1
    # Globally throttled: shed by the cheap limiter, the database never asked.
    assert [client.post("/auth/login").status_code for _ in range(5)] == [429] * 5
    assert store.calls == 1


class _TimingOutStore:
    shared = True

    def hit(self, key, now, *, window_seconds, limit):  # type: ignore[no-untyped-def]
        raise TimeoutError("pool timed out")


def test_a_pool_or_statement_timeout_is_a_503_not_an_unlimited_route() -> None:
    app = FastAPI()
    reached: list[bool] = []

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        reached.append(True)
        return {}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=False,
        production=True,
        anonymous_policy=AnonymousRateLimitPolicy.from_declaration(
            "/auth/login", requests=5, window_seconds=60
        ),
        anonymous_store=_TimingOutStore(),
    )
    client = TestClient(app)
    assert [client.post("/auth/login").status_code for _ in range(3)] == [503] * 3
    assert reached == []


# ── the limiter's own engine ───────────────────────────────────────────────


def test_dedicated_sessions_commit_on_their_own_pool_and_count(
    tmp_path: Path,
) -> None:
    from dotmac_kernel.models import Base
    from dotmac_kernel.session_runtime import DatabaseRuntime
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'limiter.db'}")
    Base.metadata.create_all(engine, tables=[RateLimitBucket.__table__])
    runtime = DatabaseRuntime(engine=engine)
    sessions = DedicatedLimiterSessions(runtime_resolver=lambda: runtime)
    store = PostgresRateLimitStore(
        key_secret=KEY_SECRET, session_provider=sessions, clock=_Clock(1_000_000_010.0)
    )

    assert [store.hit("k", 0.0, window_seconds=60, limit=2)[0] for _ in range(3)] == [
        1,
        2,
        3,
    ]
    # Committed through the limiter's own pool, visible to the platform engine.
    with runtime.platform_session() as db:
        assert db.scalars(select(RateLimitBucket)).one().hit_count == 3
    # One bounded engine, distinct from the platform engine, reused.
    ((factory,),) = [(f,) for f in runtime._bounded.values()]
    assert factory.kw["bind"] is not runtime.platform_engine
    assert factory.kw["bind"].pool.size() == 4
    assert factory.kw["bind"].pool._max_overflow == 0
    assert factory.kw["bind"].pool.timeout() == 0.5
    runtime.dispose_bounded_pools()
    assert runtime._bounded == {}


def test_a_failing_limiter_transaction_rolls_back_and_propagates(
    tmp_path: Path,
) -> None:
    from dotmac_kernel.models import Base
    from dotmac_kernel.session_runtime import DatabaseRuntime
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'limiter.db'}")
    Base.metadata.create_all(engine, tables=[RateLimitBucket.__table__])
    runtime = DatabaseRuntime(engine=engine)
    with pytest.raises(RuntimeError, match="boom"):
        with runtime.bounded_platform_session() as db:
            db.add(
                RateLimitBucket(
                    key_hash="h" * 64,
                    window_start=datetime.now(UTC),
                    hit_count=1,
                    expires_at=datetime.now(UTC),
                )
            )
            db.flush()
            raise RuntimeError("boom")
    with runtime.platform_session() as db:
        assert db.scalars(select(RateLimitBucket)).all() == []
    runtime.dispose_bounded_pools()


@pytest.mark.parametrize(
    "kw", [{"pool_size": 0}, {"pool_timeout": 0}, {"statement_timeout_ms": 0}]
)
def test_dedicated_sessions_refuse_unbounded_settings(kw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        DedicatedLimiterSessions(**kw)  # type: ignore[arg-type]


# ── every rule must match a route (startup validation) ─────────────────────


def _table() -> FastAPI:
    from fastapi import APIRouter

    app = FastAPI()

    @app.post("/auth/login")
    def login() -> dict[str, str]:
        return {}

    @app.get("/forms/{token}")
    def form(token: str) -> dict[str, str]:
        return {}

    router = APIRouter(prefix="/included")

    @router.post("/thing")
    def thing() -> dict[str, str]:
        return {}

    app.include_router(router)

    sub = FastAPI()

    @sub.post("/login")
    def sub_login() -> dict[str, str]:
        return {}

    app.mount("/api", sub)
    return app


def _errors(declaration: str, app: FastAPI | None = None) -> list[str]:
    policy = AnonymousRateLimitPolicy.from_declaration(
        declaration, requests=1, window_seconds=60
    )
    assert policy is not None
    return anonymous_rate_limit_route_errors(policy, (app or _table()).routes)


def test_rules_that_match_a_route_pass_including_include_and_mount_prefixes() -> None:
    assert (
        _errors(
            "POST /auth/login, /forms/{token}, POST /included/thing, POST /api/login"
        )
        == []
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "POST /auth/log-in",  # typo
        "GET /auth/login",  # wrong method
        "/forms/{id}",  # parameter renamed
        "POST /thing",  # include prefix forgotten
        "POST /login",  # mount prefix forgotten
        "POST /api/nowhere",  # inside a mount, no such route
    ],
)
def test_a_rule_matching_no_route_is_reported(declaration: str) -> None:
    errors = _errors(declaration)
    assert len(errors) == 1
    assert "matches no route" in errors[0]


def test_every_failing_rule_is_reported() -> None:
    assert len(_errors("POST /nope, POST /auth/login, /also-nope")) == 2


def test_a_rule_inside_a_mount_is_limited_at_runtime() -> None:
    """The template is the FULL path, so a mounted route is limited, not skipped
    (a mount used to report only its own prefix, and the rule never matched)."""
    store = _CapturingStore()
    app = _table()
    policy = AnonymousRateLimitPolicy.from_declaration(
        "POST /api/login", requests=1, window_seconds=60
    )
    app.add_middleware(
        RateLimitMiddleware,
        enabled=False,
        anonymous_policy=policy,
        anonymous_store=store,
    )
    client = TestClient(app, client=(UNTRUSTED_PEER, 5000))
    assert [client.post("/api/login").status_code for _ in range(3)] == [200, 429, 429]
    assert len(store.keys) == 1  # the blocked key never reached the store again


def test_create_app_in_production_refuses_anonymous_limits_without_request_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dotmac_kernel import app_factory
    from dotmac_kernel.api_documentation import api_documentation_policy

    monkeypatch.setattr(app_factory.settings, "environment", "production")
    monkeypatch.setattr(
        app_factory.settings, "anonymous_rate_limit_routes", "POST /auth/login"
    )
    monkeypatch.setattr(app_factory.settings, "anonymous_rate_limit_store", "postgres")
    monkeypatch.setattr(
        app_factory.settings, "anonymous_rate_limit_key_secret", KEY_SECRET
    )
    spec = ProductAssemblySpec(
        api_documentation=api_documentation_policy("development"), name="k2-no-evidence"
    )

    with pytest.raises(AnonymousRateLimitConfigurationError, match="request_evidence"):
        create_app(spec)
