"""A ceremony store may be bound to a REQUEST, not only to the process.

## Why this seam exists

The client is built once and reused, because it owns the `ProviderCache`: build
one per request and every sign-in costs two extra calls to the provider, and the
`kid`-rotation refresh — which is a property of a cache that outlives a
request — goes with them.

But the most defensible store a consumer can offer is its own database, and a
database store is bound to ONE request's transaction. The kernel's
`dotmac_kernel.db` decides when a transaction opens and commits (hard rule 8);
a library that held a `Session` and wrote through it on its own schedule would
be a second transaction authority. The ceremony would commit at a different
moment from everything else the request did, and a rolled-back request would
leave a live ceremony behind.

So the store is supplied per ceremony operation, and the client keeps the cache.
Neither half moved to the other side.

## What must NOT be possible

A client with no store at all. `PER_REQUEST_STATE_STORE` is a POSITIVE
declaration — passing it says "I supply one per call", so forgetting the
argument is a `ConfigurationError` naming the parameter, at the call, rather
than a login that silently loses its PKCE verifier. The sentinel implements
neither `put` nor `take` precisely so that a consumer who confuses it for a
store cannot get a half-working one.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from dotmac_auth_oidc import (
    PER_REQUEST_STATE_STORE,
    ConfigurationError,
    InMemoryStateStore,
    LoginState,
    OIDCClient,
    OIDCError,
    PerRequestStateStore,
    RelyingPartyConfig,
    StateError,
    StateStore,
)

ISSUER = "https://idp.example.com"
REDIRECT = "https://app.example.com/auth/callback"


class CountingTransport:
    """Serves discovery, and counts how often it is asked.

    The transport is the package's own injection seam, so the client's cache is
    exercised for real rather than replaced. That matters here: whether the
    cache survives a per-call store is one of the properties under test, and a
    monkeypatched cache could not show it. (`ProviderCache` uses `__slots__`, so
    patching an instance attribute is not available either — the design says
    inject the transport, and it means it.)
    """

    def __init__(self) -> None:
        self.discovery_fetches = 0

    def get_json(self, url: str, *, timeout: float) -> dict[str, object]:
        if url.endswith("/.well-known/openid-configuration"):
            self.discovery_fetches += 1
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            }
        raise AssertionError(f"unexpected GET {url}")

    def post_form(
        self, url: str, *, data: dict[str, str], auth: Any, timeout: float
    ) -> dict[str, object]:
        # No id_token: the exchange is not what this file is about, and
        # reaching it at all is the assertion in the tests that get this far.
        return {"access_token": "irrelevant"}


def _config() -> RelyingPartyConfig:
    return RelyingPartyConfig(
        provider_binding="corp-idp",
        issuer=ISSUER,
        client_id="dotmac-test-client",
        client_secret="shh",
        redirect_uri=REDIRECT,
    )


def _client(store: Any, transport: CountingTransport | None = None) -> OIDCClient:
    return OIDCClient(
        _config(), state_store=store, transport=transport or CountingTransport()
    )


class RecordingStore:
    """A `StateStore` that remembers which instance was used.

    Stands in for a request-bound store: in production this is the one holding
    the request's `Session`, and the property that matters is that THIS one is
    written to and read from, never some other.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.held: dict[str, LoginState] = {}
        self.puts = 0
        self.takes = 0

    def put(self, state: LoginState, *, ttl_seconds: int) -> None:
        self.puts += 1
        self.held[state.state_id] = state

    def take(self, state_id: str) -> LoginState | None:
        self.takes += 1
        return self.held.pop(state_id, None)


# ── The declaration ─────────────────────────────────────────────────────────


def test_a_client_with_no_store_at_all_is_still_refused() -> None:
    """The invariant this seam must not weaken. `None` is not "per request"."""
    with pytest.raises(ConfigurationError, match="StateStore is required"):
        OIDCClient(_config(), state_store=None)  # type: ignore[arg-type]


def test_the_sentinel_is_not_a_store_and_cannot_be_mistaken_for_one() -> None:
    """No `put`, no `take`. A consumer who passes it where a store belongs gets
    an `AttributeError` at the seam rather than a store-shaped object that
    quietly holds nothing."""
    assert not hasattr(PER_REQUEST_STATE_STORE, "put")
    assert not hasattr(PER_REQUEST_STATE_STORE, "take")
    assert isinstance(PER_REQUEST_STATE_STORE, PerRequestStateStore)


def test_declaring_per_request_and_then_forgetting_it_is_an_error() -> None:
    """The whole point of the declaration: the mistake is loud, and it names the
    argument that is missing."""
    client = _client(PER_REQUEST_STATE_STORE)

    with pytest.raises(ConfigurationError, match="state_store must be passed"):
        client.start_login(return_to="/")
    with pytest.raises(ConfigurationError, match="state_store must be passed"):
        client.complete_login(
            code="c", state_parameter="s", stored_state="s", state_store=None
        )


# ── The store that is actually used ─────────────────────────────────────────


def test_the_per_call_store_is_the_one_the_ceremony_lands_in() -> None:
    client = _client(PER_REQUEST_STATE_STORE)
    request_store = RecordingStore("request-1")

    redirect = client.start_login(return_to="/here", state_store=request_store)

    assert request_store.puts == 1
    assert redirect.state in request_store.held
    assert request_store.held[redirect.state].return_to == "/here"


def test_two_requests_use_their_own_stores_and_never_each_others() -> None:
    """The property a request-bound store exists for.

    One client, two in-flight logins, two transactions. A client that had
    latched onto the first store would write the second ceremony into the first
    request's transaction — which by then may have committed, or rolled back.
    """
    client = _client(PER_REQUEST_STATE_STORE)
    first, second = RecordingStore("request-1"), RecordingStore("request-2")

    a = client.start_login(return_to="/a", state_store=first)
    b = client.start_login(return_to="/b", state_store=second)

    assert list(first.held) == [a.state]
    assert list(second.held) == [b.state]


def test_a_per_call_store_overrides_a_held_one() -> None:
    """A consumer holding a process-lifetime store can still redirect a single
    ceremony elsewhere without a second client — and so without a second
    provider cache."""
    held = InMemoryStateStore()
    client = _client(held)
    per_call = RecordingStore("just-this-one")

    redirect = client.start_login(return_to="/", state_store=per_call)

    assert redirect.state in per_call.held
    assert held.take(redirect.state) is None, (
        "the ceremony was also written to the client's held store — a per-call "
        "store must override, not duplicate"
    )


def test_a_held_store_still_works_with_no_per_call_argument() -> None:
    """The existing shape is unchanged. This seam adds an option; it does not
    make every consumer thread a store through every call."""
    held = InMemoryStateStore()
    client = _client(held)

    redirect = client.start_login(return_to="/")

    assert held.take(redirect.state) is not None


# ── The callback half ───────────────────────────────────────────────────────


def test_the_callback_claims_from_the_store_it_was_given() -> None:
    """A login started in one request is finished in another, so the two stores
    are DIFFERENT objects over the same shared rows. Here the second store is
    seeded with what the first wrote, which is what a database does for free and
    what a per-worker store cannot do at all.
    """
    client = _client(PER_REQUEST_STATE_STORE)
    starting = RecordingStore("request-1")
    redirect = client.start_login(return_to="/", state_store=starting)

    finishing = RecordingStore("request-2")
    finishing.held = dict(starting.held)  # the shared rows both requests see

    # The exchange is not what this file tests, and there is no provider here
    # to answer it. Reaching the exchange at all is the assertion: the claim
    # succeeded, from the store this call was handed.
    with pytest.raises(OIDCError) as exc:
        client.complete_login(
            code="an-authorization-code",
            state_parameter=redirect.state,
            stored_state=redirect.state,
            state_store=finishing,
        )
    assert not isinstance(exc.value, ConfigurationError | StateError)
    assert finishing.takes == 1
    assert redirect.state not in finishing.held, "the ceremony was not consumed"


def test_the_state_pair_is_checked_before_the_ceremony_is_claimed() -> None:
    """Ordering, and it is load-bearing.

    If claiming came first, anyone able to reach the callback with a valid state
    and a wrong cookie could destroy somebody else's in-flight login. Refusing
    an attacker must not cost the legitimate user their sign-in.
    """
    client = _client(PER_REQUEST_STATE_STORE)
    store = RecordingStore("request-1")
    redirect = client.start_login(return_to="/", state_store=store)

    with pytest.raises(StateError, match="not the one this browser"):
        client.complete_login(
            code="an-authorization-code",
            state_parameter=redirect.state,
            stored_state="a-cookie-from-somewhere-else",
            state_store=store,
        )

    assert store.takes == 0, "a mismatched pair reached the store"
    assert redirect.state in store.held, (
        "the mismatched callback consumed a ceremony that belongs to somebody "
        "else — that is a denial of service on their login"
    )


# ── The cache the client keeps ──────────────────────────────────────────────


def test_the_provider_cache_is_not_rebuilt_per_ceremony() -> None:
    """The reason the STORE moved rather than the client.

    Counted at the transport, which is the only place a refetch would show. A
    client rebuilt per request would fetch discovery on every login; here three
    ceremonies through three different stores cost exactly one fetch.
    """
    transport = CountingTransport()
    client = _client(PER_REQUEST_STATE_STORE, transport)

    for index in range(3):
        client.start_login(return_to="/", state_store=RecordingStore(f"r{index}"))

    assert transport.discovery_fetches == 1, (
        f"{transport.discovery_fetches} discovery fetches for 3 ceremonies — "
        "the per-call store disturbed the cache the client exists to keep"
    )


def test_a_protocol_conforming_store_needs_no_base_class() -> None:
    """`StateStore` is a `Protocol`, so a consumer's adapter — the object that
    holds the request's database session — satisfies it structurally. Nothing
    in this package needs importing to implement one."""
    store: StateStore = RecordingStore("structural")
    state = LoginState(
        state_id="an-id",
        nonce="a-nonce",
        code_verifier="v" * 43,
        redirect_uri=REDIRECT,
        issued_at=int(time.time()),
    )
    store.put(state, ttl_seconds=600)
    assert store.take("an-id") is state
