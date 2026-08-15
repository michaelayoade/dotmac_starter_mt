"""PKCE, and the server-side ceremony a login is resumed from.

The relying party has no session yet when a login starts — that is the whole
problem this module solves. Something must survive the round trip to the
provider and let the callback recover the PKCE verifier and the nonce.

**The ceremony never travels.** The `state` parameter on the wire is a random
opaque id and nothing else; the `LoginState` it names is held server-side in a
`StateStore` and removed the first time it is claimed. So the front channel
carries no verifier, no nonce and no return path — there is nothing in it to
read, and nothing to encrypt.

That is a correction to an earlier design here, and the reasoning is worth
keeping. The first version signed the ceremony into the state parameter, which
made it tamper-evident but still **readable by anything that saw the URL** — a
referrer header, a proxy log, browser history, a shoulder. Its defence was that
the state travels in an `HttpOnly` cookie, which is a property of the CONSUMER's
integration rather than of this package, and "confidential as long as every
consumer holds it right" is not a security property a library gets to claim. A
random id has nothing to leak, so the question disappears instead of being
managed.

It also removes a whole moving part: with the ceremony server-side there is no
payload to sign, so there is no signing key, no domain separator and no key
separation to get wrong. ERP's OIDC state is signed with the HOST's session-JWT
secret (`from app.services.auth_flow import _jwt_secret`), making the two
protocols a forgery oracle for each other. The safest version of that mistake is
not making a better key — it is having no key.

The store is REQUIRED, not optional. Replay protection is not a feature a
deployment opts into: with the ceremony held server-side, `take` IS how the
verifier is recovered, so a replayed callback finds nothing and single use is
structural rather than an added check.

## A store may be process-lifetime or REQUEST-BOUND

Some stores are built once and live as long as the process — Redis, say, whose
client is a connection pool. Others cannot be: a store backed by the
consumer's own database must write through THAT REQUEST's transaction, because
the consumer's framework owns when a transaction opens and commits and a
library that opened its own would be a second transaction authority — the
ceremony would commit at a different moment from everything else the request
did, and a rolled-back request would leave a live ceremony behind.

So a `StateStore` may be supplied per ceremony operation instead of at
construction. What may NOT happen is neither: `PER_REQUEST_STATE_STORE` is a
positive declaration that the consumer supplies one per call, so a client with
no store is still a configuration error at construction rather than a surprise
at the first login. See `OIDCClient.start_login`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from dotmac_auth_oidc.errors import StateError, StateUnavailableError

DEFAULT_STATE_TTL_SECONDS = 600

# 32 bytes of urlsafe randomness. The state id is the ONLY thing standing
# between a callback and a stored ceremony, so it is sized as a bearer secret
# rather than as a correlation id.
_STATE_ID_BYTES = 32


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class PKCEPair:
    verifier: str
    challenge: str

    @property
    def method(self) -> str:
        return "S256"


def generate_pkce() -> PKCEPair:
    """A fresh PKCE verifier and its S256 challenge.

    S256 only. `plain` is in the RFC and is worthless — it sends the verifier to
    the provider in the authorization request, which is the exact interception
    PKCE exists to defeat. There is no parameter to select it.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return PKCEPair(verifier=verifier, challenge=_b64url_encode(digest))


def generate_state_id() -> str:
    return secrets.token_urlsafe(_STATE_ID_BYTES)


@dataclass(frozen=True, slots=True)
class LoginState:
    """One in-flight login. **Held server-side; never serialized to the wire.**

    `code_verifier` is the reason this dataclass does not travel: possession of
    it plus an intercepted authorization code completes the exchange, which is
    precisely what PKCE exists to prevent.
    """

    state_id: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    issued_at: int
    return_to: str = "/"

    def expired(self, *, ttl_seconds: int, now: int | None = None) -> bool:
        clock = int(time.time()) if now is None else now
        return clock - self.issued_at > ttl_seconds


class StateStore(Protocol):
    """Where in-flight ceremonies live. **Required — there is no null store.**

    `take` MUST be atomic: a read-then-delete that another request can interleave
    with is not single use, and two concurrent callbacks would each recover the
    verifier. Redis `GETDEL`, or `DELETE ... RETURNING` on a row, both qualify.

    It must also be SHARED across every process serving the callback route. A
    per-worker store is not merely weaker — it is broken: a login started on one
    worker cannot be completed on another.
    """

    def put(self, state: LoginState, *, ttl_seconds: int) -> None:
        """Hold `state` under its own `state_id` for at most `ttl_seconds`."""
        ...

    def take(self, state_id: str) -> LoginState | None:
        """Atomically remove and return the ceremony, or None if it is not
        there — unknown id, already claimed, or expired. All three are the same
        answer to a caller: this callback cannot be completed."""
        ...


class PerRequestStateStore:
    """The sentinel type — use the `PER_REQUEST_STATE_STORE` singleton.

    Public because it appears in `OIDCClient.__init__`'s signature, and a type
    a consumer cannot name is a type they cannot annotate around.

    Deliberately implements NEITHER `put` nor `take`.

    If it did, a consumer that passed it and then forgot the per-call argument
    would get a store-shaped object that silently did the wrong thing. With no
    methods, the same mistake is an explicit `ConfigurationError` naming the
    parameter that is missing.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "PER_REQUEST_STATE_STORE"


#: Declares that this client's ceremony store is supplied per operation rather
#: than held for the life of the process. Pass it as `state_store=` when the
#: real store is bound to a request — a database session, most often. It is not
#: a store and cannot be used as one.
PER_REQUEST_STATE_STORE = PerRequestStateStore()


class InMemoryStateStore:
    """A `StateStore` in one process — **tests and single-worker development
    only.**

    Not production, and the failure is not subtle: with two workers behind a
    load balancer, a login started on one and completed on the other finds
    nothing and is refused, so a share of logins fail at random. It is left
    loudly broken rather than quietly weakened — there is no fallback that would
    let a callback succeed without its stored ceremony.
    """

    __slots__ = ("_held",)

    def __init__(self) -> None:
        self._held: dict[str, tuple[float, LoginState]] = {}

    def put(self, state: LoginState, *, ttl_seconds: int) -> None:
        self._held[state.state_id] = (time.monotonic() + ttl_seconds, state)
        if len(self._held) > 10_000:
            now = time.monotonic()
            self._held = {
                key: value for key, value in self._held.items() if value[0] > now
            }

    def take(self, state_id: str) -> LoginState | None:
        # `dict.pop` is atomic under the GIL, which is the whole reason this
        # single-process implementation is correct within its stated scope.
        entry = self._held.pop(state_id, None)
        if entry is None:
            return None
        expires_at, state = entry
        if expires_at <= time.monotonic():
            return None
        return state


def claim_state(store: StateStore, state_id: str, *, ttl_seconds: int) -> LoginState:
    """Recover and consume the ceremony named by `state_id`.

    Raises `StateUnavailableError` when the store has nothing. That covers a
    replayed callback, a state this deployment never issued, and one that
    expired out of the store — and the name stays neutral because the store
    cannot tell them apart. Claiming "replay" here would emit a confident
    attack signal for what is usually a back button.

    Not distinguishing them is also deliberate downstream: it would let an
    attacker probe whether a given state id was ever real.

    Raises `StateError` if the recovered ceremony is itself past its TTL, which
    a store with coarse or absent expiry can allow.
    """
    if not state_id:
        raise StateError("callback carried no state")
    state = store.take(state_id)
    if state is None:
        raise StateUnavailableError(
            "this login cannot be completed — its state was already used, has "
            "expired, or was never issued by this deployment"
        )
    if state.expired(ttl_seconds=ttl_seconds):
        raise StateError("state has expired")
    return state


__all__ = [
    "DEFAULT_STATE_TTL_SECONDS",
    "PER_REQUEST_STATE_STORE",
    "InMemoryStateStore",
    "PerRequestStateStore",
    "LoginState",
    "PKCEPair",
    "StateStore",
    "claim_state",
    "generate_pkce",
    "generate_state_id",
]
