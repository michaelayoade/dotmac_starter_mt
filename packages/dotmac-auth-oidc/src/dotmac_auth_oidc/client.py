"""The relying-party ceremony: start a login, finish one, return a subject.

The output of this whole package is `VerifiedSubject` — an issuer and a subject
string, and the raw claims for a caller that wants display evidence. That is the
entire contract. What happens next is somebody else's decision:

    dotmac-auth-oidc                 →  verified (issuer, subject)
    dotmac_kernel.external_identity  →  which local Party is that, here?
    the product's identity facet     →  issue ITS OWN session

This package never touches the third step. It queries no local table, mints no
session, sets no cookie and creates no user. ERP's client does the first and the
third inline (`oidc.py:322-337` queries `FederatedIdentity` and `Person`;
`auth_web.py:335-347` issues tokens), which is the coupling that makes it
unreusable and is deliberately not ported.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from dotmac_auth_oidc.discovery import ProviderCache, require_https_url
from dotmac_auth_oidc.errors import (
    ConfigurationError,
    IDTokenError,
    NonceMismatchError,
    StateError,
    TokenExchangeError,
    UnsupportedAlgorithmError,
)
from dotmac_auth_oidc.state import (
    DEFAULT_STATE_TTL_SECONDS,
    LoginState,
    StateStore,
    claim_state,
    generate_pkce,
    generate_state_id,
)
from dotmac_auth_oidc.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpxTransport,
    Transport,
)

# Asymmetric only, and not configurable. A relying party that accepts an HMAC
# algorithm can be defeated by signing a token with the provider's PUBLIC key as
# the HMAC secret — the key is published in the JWKS, so the "secret" is known to
# everyone. `none` needs no explanation. Making this a constructor argument would
# turn a fleet-wide invariant into a per-deployment mistake.
ALLOWED_ALGORITHMS: frozenset[str] = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
)

# Tolerance for `exp`/`iat`/`nbf`. ERP has none, so a provider whose clock is a
# second fast rejects every login. Small enough to be irrelevant to an attacker
# and large enough to absorb ordinary NTP drift.
DEFAULT_LEEWAY_SECONDS = 60

DEFAULT_SCOPES = ("openid", "profile", "email")


@dataclass(frozen=True, slots=True)
class VerifiedSubject:
    """A subject this package is willing to say authenticated.

    `issuer` and `subject` together are the only identity assertion here. The
    pair is opaque and stable; it is NOT an email, a username, or anything a
    human chose, and it must not be treated as one.

    `claims` is present for display evidence — a name to show, an email to
    prefill. Consuming a role, group, scope or organization claim from it as an
    AUTHORIZATION input is the mistake this whole package is shaped to prevent;
    authorization is local, and `dotmac_kernel.deps.authorize_party` decides it
    from local grants.

    `return_to` comes back from the STORED ceremony, so it is the value this
    deployment put there rather than anything the browser or provider could
    influence. It is still the consumer's job to validate it against its own
    routes before redirecting.
    """

    issuer: str
    subject: str
    claims: dict[str, Any] = field(default_factory=dict)
    return_to: str = "/"

    @property
    def email(self) -> str | None:
        """The provider's email claim, or None. **Display evidence only.**

        Never a linking key. Matching on it would let anyone who can control an
        IdP account bearing a known address inherit that person's local identity
        — ERP's contract states the rule and
        `dotmac_kernel.external_identity` enforces it by having no
        match-by-email path at all.
        """
        value = self.claims.get("email")
        return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class RelyingPartyConfig:
    """One configured provider registration, from the consumer's own config.

    `provider_binding` is the local name of this registration, and it is what a
    caller passes to `dotmac_kernel.external_identity.resolve_external_identity`
    alongside the verified subject. It is carried here so a consumer with two
    providers cannot mix up which one completed a ceremony — the value travels
    with the config object rather than being reconstructed at the call site.
    """

    provider_binding: str
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    discovery_url: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider_binding", "issuer", "client_id", "redirect_uri"):
            if not str(getattr(self, name)).strip():
                raise ConfigurationError(f"relying-party config needs a {name!r}")
        require_https_url(self.issuer, field="issuer", error=ConfigurationError)
        # The override exists so a provider whose document is not at the
        # conventional well-known path can still be used. It is still the URL
        # this client fetches signing-key metadata from, so it gets the same
        # rule the DISCOVERED endpoints get — otherwise an operator typo
        # silently downgrades the one fetch that bootstraps every other one,
        # and the network chooses the token endpoint and the key set.
        if self.discovery_url is not None:
            require_https_url(
                self.discovery_url,
                field="discovery_url override",
                error=ConfigurationError,
            )
        if "openid" not in self.scopes:
            raise ConfigurationError(
                "scopes must include 'openid' — without it the provider runs a "
                "plain OAuth2 flow and returns no ID token to verify"
            )


@dataclass(frozen=True, slots=True)
class AuthorizationRedirect:
    """Where to send the browser, and the opaque id to keep until it returns.

    `state` is a random identifier and carries no ceremony data at all. Store it
    in an `HttpOnly` cookie so the callback can prove the login started in this
    browser; there is nothing else in it to protect.
    """

    url: str
    state: str


class OIDCClient:
    """A relying party for ONE configured provider.

    Holds no per-login state of its own — every in-flight ceremony lives in the
    `StateStore`, which is REQUIRED rather than optional.
    """

    __slots__ = ("_cache", "_config", "_leeway", "_store", "_timeout")

    def __init__(
        self,
        config: RelyingPartyConfig,
        *,
        state_store: StateStore,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        leeway: int = DEFAULT_LEEWAY_SECONDS,
    ) -> None:
        if state_store is None:
            raise ConfigurationError(
                "a StateStore is required: it holds the PKCE verifier, so there "
                "is no login without one"
            )
        self._config = config
        self._store = state_store
        self._timeout = timeout
        self._leeway = leeway
        self._cache = ProviderCache(
            config.issuer,
            transport=transport or HttpxTransport(),
            discovery_override=config.discovery_url,
            timeout=timeout,
        )

    @property
    def config(self) -> RelyingPartyConfig:
        return self._config

    def start_login(
        self, *, return_to: str = "/", ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS
    ) -> AuthorizationRedirect:
        """Build the authorization redirect and store the ceremony server-side.

        `return_to` is kept in the STORED state and never put on the wire. It is
        not validated here — this package does not know which paths are safe in
        the consumer's application, and a library that guessed would either
        block legitimate targets or wave through an open redirect. Validate it
        on the way out, as ERP's `_sanitize_redirect_url` does.
        """
        pkce = generate_pkce()
        state = LoginState(
            state_id=generate_state_id(),
            nonce=secrets.token_urlsafe(32),
            code_verifier=pkce.verifier,
            redirect_uri=self._config.redirect_uri,
            issued_at=int(time.time()),
            return_to=return_to,
        )
        self._store.put(state, ttl_seconds=ttl_seconds)

        metadata = self._cache.metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._config.client_id,
                "redirect_uri": self._config.redirect_uri,
                "scope": " ".join(self._config.scopes),
                "state": state.state_id,
                "nonce": state.nonce,
                "code_challenge": pkce.challenge,
                "code_challenge_method": pkce.method,
            }
        )
        separator = "&" if "?" in metadata.authorization_endpoint else "?"
        return AuthorizationRedirect(
            url=f"{metadata.authorization_endpoint}{separator}{query}",
            state=state.state_id,
        )

    def complete_login(
        self,
        *,
        code: str,
        state_parameter: str,
        stored_state: str,
        ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS,
    ) -> VerifiedSubject:
        """Finish the ceremony and return the verified subject.

        `stored_state` is what the consumer kept (its HttpOnly cookie);
        `state_parameter` is what came back on the query string. Both are
        required and must match: the cookie proves the login started in THIS
        browser, and the query parameter proves the provider echoed it. Checking
        only one of them leaves login CSRF open — an attacker completes their
        own login in a victim's browser, silently binding the victim's session
        to the attacker's identity.

        Order is deliberate and each step is a distinct refusal:
        state match → claim (single use) → code exchange → ID-token signature →
        claims → nonce.
        """
        if not code:
            raise TokenExchangeError("callback carried no authorization code")
        if not state_parameter or not stored_state:
            raise StateError("callback is missing its state or stored state")
        if not secrets.compare_digest(state_parameter, stored_state):
            raise StateError(
                "the state on the callback is not the one this browser started "
                "the login with"
            )

        # Claiming is what RECOVERS the PKCE verifier, so single use is
        # structural rather than an added check: a replayed callback finds
        # nothing to exchange with.
        state = claim_state(self._store, stored_state, ttl_seconds=ttl_seconds)

        tokens = self._exchange_code(code=code, state=state)
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise TokenExchangeError(
                "token response carried no id_token — the provider ran an OAuth2 "
                "flow, not an OIDC one"
            )

        claims = self._validate_id_token(id_token)

        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, state.nonce):
            raise NonceMismatchError(
                "the ID token's nonce is not this login's — the token is "
                "genuine but belongs to a different ceremony"
            )

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IDTokenError("the ID token carries no usable `sub`")

        return VerifiedSubject(
            issuer=self._cache.metadata().issuer,
            subject=subject,
            claims=claims,
            return_to=state.return_to,
        )

    def _exchange_code(self, *, code: str, state: LoginState) -> dict[str, Any]:
        metadata = self._cache.metadata()
        try:
            return self._cache.transport.post_form(
                metadata.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": state.redirect_uri,
                    "client_id": self._config.client_id,
                    "code_verifier": state.code_verifier,
                },
                auth=(self._config.client_id, self._config.client_secret),
                timeout=self._timeout,
            )
        except Exception as exc:
            raise TokenExchangeError(f"code exchange failed: {exc}") from exc

    def _validate_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify signature and claims. The security core of the package.

        This is precisely the function ERP monkeypatches out of every one of its
        tests, so none of the checks below has ever been exercised there. Here it
        runs against a real key pair in the suite.
        """
        import jwt

        try:
            header = jwt.get_unverified_header(id_token)
        except Exception as exc:
            raise IDTokenError(f"ID token header is unreadable: {exc}") from exc

        algorithm = header.get("alg")
        # Checked BEFORE the key is fetched, so an `alg: none` token never even
        # reaches key selection.
        if not isinstance(algorithm, str) or algorithm not in ALLOWED_ALGORITHMS:
            raise UnsupportedAlgorithmError(
                f"ID token algorithm {algorithm!r} is not permitted — only "
                "asymmetric signatures are accepted"
            )
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise IDTokenError(
                "ID token names no `kid` — the signing key is unaddressable, and "
                "trying every published key until one verifies is not validation"
            )

        jwk = self._cache.signing_key(kid)
        try:
            key = jwt.PyJWK(jwk)
        except Exception as exc:
            raise IDTokenError(f"provider key {kid!r} is unusable: {exc}") from exc

        try:
            claims = jwt.decode(
                id_token,
                # The PyJWK ITSELF, not its unwrapped `.key`. The JWK carries the
                # key's own `kty`/`alg` binding, so PyJWT can refuse a token whose
                # algorithm does not match the key it names — an EC signature
                # verified against an RSA key, say. Unwrapping to `.key` throws
                # that binding away and leaves `algorithms=` as the only check.
                key=key,
                # The single verified algorithm, not the whole allowlist: a token
                # must be verified with the algorithm it declared and that we
                # accepted, never merely one of several we would tolerate.
                algorithms=[algorithm],
                audience=self._config.client_id,
                issuer=self._cache.metadata().issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iat", "sub", "aud", "iss"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise IDTokenError(f"ID token failed validation: {exc}") from exc

        if not isinstance(claims, dict):
            raise IDTokenError("ID token payload is not an object")

        # `azp` matters exactly when `aud` is multi-valued: the token was issued
        # for several audiences, and `azp` names the one it was actually
        # authorized for. PyJWT is satisfied by our client_id appearing anywhere
        # in `aud`, which would let a token minted for a DIFFERENT relying party
        # that merely lists us be replayed here.
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1:
            authorized = claims.get("azp")
            if authorized != self._config.client_id:
                raise IDTokenError(
                    "ID token has multiple audiences and its `azp` is not this "
                    "client — it was authorized for a different relying party"
                )
        return claims


__all__ = [
    "ALLOWED_ALGORITHMS",
    "DEFAULT_LEEWAY_SECONDS",
    "DEFAULT_SCOPES",
    "AuthorizationRedirect",
    "OIDCClient",
    "RelyingPartyConfig",
    "VerifiedSubject",
]
