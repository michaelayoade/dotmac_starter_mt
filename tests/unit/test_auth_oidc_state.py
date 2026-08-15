"""PKCE, and the server-side ceremony a login is resumed from.

The state parameter is the only thing carrying a login across the redirect, at a
point where the relying party has no session yet. Two ERP defects shaped this
design, and the tests below are the regression guards for both:

- ERP signs its state with the HOST's session-JWT secret
  (`from app.services.auth_flow import _jwt_secret`), making the two protocols a
  forgery oracle for each other;
- ERP's state is replayable for its whole 600-second TTL, with only the
  provider's code-single-use rule standing in the way.

Both are answered structurally rather than by a better key: the ceremony never
leaves the server, so there is no payload to sign and nothing on the wire to
protect, and claiming the stored state IS how the verifier is recovered, so a
replay finds nothing to exchange with.
"""

from __future__ import annotations

import base64
import hashlib
import time

import pytest
from dotmac_auth_oidc import (
    InMemoryStateStore,
    LoginState,
    StateError,
    StateUnavailableError,
    claim_state,
    generate_pkce,
    generate_state_id,
)


def _state(**over: object) -> LoginState:
    base = {
        "state_id": generate_state_id(),
        "nonce": "nonce-1",
        "code_verifier": "verifier-1",
        "redirect_uri": "https://app.example.com/cb",
        "issued_at": int(time.time()),
        "return_to": "/dashboard",
    }
    base.update(over)
    return LoginState(**base)  # type: ignore[arg-type]


# ── PKCE ────────────────────────────────────────────────────────────────────


def test_pkce_is_s256_and_the_challenge_is_the_hash_not_the_verifier() -> None:
    """`plain` PKCE sends the verifier in the authorization request, which is
    exactly the interception PKCE exists to defeat. There is no way to ask for
    it here, and the challenge must not be the verifier."""
    pair = generate_pkce()
    assert pair.method == "S256"
    assert pair.challenge != pair.verifier

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert pair.challenge == expected


def test_every_pkce_pair_is_fresh() -> None:
    assert generate_pkce().verifier != generate_pkce().verifier


# ── The state id is opaque and carries nothing ──────────────────────────────


def test_a_state_id_is_unguessable_and_unique() -> None:
    """It is the ONLY thing between a callback and a stored ceremony, so it is
    sized as a bearer secret rather than as a correlation id."""
    ids = {generate_state_id() for _ in range(200)}
    assert len(ids) == 200
    # 32 random bytes, urlsafe-base64 → 43 chars.
    assert all(len(value) >= 43 for value in ids)


def test_the_state_id_leaks_no_part_of_the_ceremony() -> None:
    """The property the earlier signed-state design could not have.

    The verifier, the nonce and the return path must appear nowhere in the value
    that travels — not encoded, not encrypted, not at all. A reader of the URL
    learns only that a login is in flight.
    """
    state = _state(
        nonce="the-secret-nonce",
        code_verifier="the-secret-verifier",
        return_to="/somewhere/private",
    )
    travelling = state.state_id

    for secret in ("the-secret-nonce", "the-secret-verifier", "/somewhere/private"):
        assert secret not in travelling
        # Nor in any obvious encoding of it.
        encoded = base64.urlsafe_b64encode(secret.encode()).rstrip(b"=").decode()
        assert encoded not in travelling


# ── Single use ──────────────────────────────────────────────────────────────


def test_a_stored_ceremony_can_be_claimed_once() -> None:
    store = InMemoryStateStore()
    state = _state()
    store.put(state, ttl_seconds=600)

    claimed = claim_state(store, state.state_id, ttl_seconds=600)
    assert claimed.code_verifier == "verifier-1"

    with pytest.raises(StateUnavailableError):
        claim_state(store, state.state_id, ttl_seconds=600)


def test_claiming_is_what_recovers_the_verifier() -> None:
    """Why single use is structural rather than an added check: there is no
    other path to the verifier, so a replayed callback has nothing to exchange
    the authorization code with."""
    store = InMemoryStateStore()
    state = _state()
    store.put(state, ttl_seconds=600)

    assert claim_state(store, state.state_id, ttl_seconds=600).code_verifier
    assert store.take(state.state_id) is None


def test_a_state_never_issued_here_is_refused() -> None:
    """Not only replay: a well-formed id this deployment never issued — from
    another instance, or invented — finds nothing and is refused."""
    with pytest.raises(StateUnavailableError):
        claim_state(InMemoryStateStore(), generate_state_id(), ttl_seconds=600)


def test_an_empty_state_is_refused_before_the_store_is_touched() -> None:
    with pytest.raises(StateError):
        claim_state(InMemoryStateStore(), "", ttl_seconds=600)


def test_replay_expiry_and_never_issued_are_indistinguishable_to_a_caller() -> None:
    """Deliberate: telling them apart would let an attacker probe whether a
    given state id was ever real."""
    store = InMemoryStateStore()
    used = _state()
    store.put(used, ttl_seconds=600)
    claim_state(store, used.state_id, ttl_seconds=600)

    with pytest.raises(StateUnavailableError) as replayed:
        claim_state(store, used.state_id, ttl_seconds=600)
    with pytest.raises(StateUnavailableError) as unknown:
        claim_state(store, generate_state_id(), ttl_seconds=600)

    assert str(replayed.value) == str(unknown.value)


# ── Expiry ──────────────────────────────────────────────────────────────────


def test_an_expired_ceremony_is_refused_even_if_the_store_still_holds_it() -> None:
    """Belt and braces over the store's own TTL: a store with coarse or absent
    expiry must not extend a login's life."""
    store = InMemoryStateStore()
    stale = _state(issued_at=int(time.time()) - 3600)
    store.put(stale, ttl_seconds=86_400)  # store would still serve it

    with pytest.raises(StateError, match="expired"):
        claim_state(store, stale.state_id, ttl_seconds=60)


def test_the_store_expires_entries_on_its_own_ttl() -> None:
    store = InMemoryStateStore()
    state = _state()
    store.put(state, ttl_seconds=0)
    assert store.take(state.state_id) is None


def test_expired_is_reported_as_expired_when_the_store_still_had_it() -> None:
    """The one case that is distinguishable, and safely so: the store DID hold
    it, so its existence is already established — only its age refused it."""
    store = InMemoryStateStore()
    stale = _state(issued_at=int(time.time()) - 3600)
    store.put(stale, ttl_seconds=86_400)

    with pytest.raises(StateError) as exc:
        claim_state(store, stale.state_id, ttl_seconds=60)
    assert not isinstance(exc.value, StateUnavailableError)
