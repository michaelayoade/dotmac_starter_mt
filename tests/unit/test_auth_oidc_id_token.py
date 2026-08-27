"""ID-token validation, against a REAL key pair.

This file exists because of a specific finding. ERP's `_validate_id_token` and
`_exchange_code` are monkeypatched out in every one of its tests, so the
security-critical core of the fleet's only OIDC client has never been executed
by a test: not the signature check, not the algorithm allowlist, not `kid`
handling, not one claim-validation failure path.

Here the transport is injected instead of the validator being replaced, so every
test below runs the actual verification code against tokens signed with an
actual RSA key.

The attack the allowlist exists for is worth naming precisely, because it looks
like a compatibility setting: a provider publishes its RSA *public* key in the
JWKS, so anyone can read it. If the relying party accepts `HS256`, an attacker
signs a token using those public key bytes as the HMAC secret, and a naive
validator — which fetches the key by `kid` and verifies with whatever `alg` the
token asked for — accepts it. The test named for that forgery below performs
it exactly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from dotmac_auth_oidc import (
    IDTokenError,
    InMemoryStateStore,
    NonceMismatchError,
    OIDCClient,
    RelyingPartyConfig,
    StateError,
    StateUnavailableError,
    TokenExchangeError,
    UnsupportedAlgorithmError,
)

# Imported NORMALLY, never through `importorskip`. `pyjwt[crypto]` is a declared
# runtime dependency of the package under test, so if it is missing these tests
# must FAIL — skipping would turn "the security core is unverifiable here" into a
# green run, which is the exact shape of ERP's defect: its equivalents are
# monkeypatched away and its suite passes without ever validating a token.

ISSUER = "https://idp.example.com"
CLIENT_ID = "dotmac-test-client"
REDIRECT = "https://app.example.com/auth/callback"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(keypair: Any) -> dict[str, Any]:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(keypair.public_key()))
    public_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [public_jwk]}


class FakeTransport:
    """Serves a fixed discovery document, key set and token response."""

    def __init__(self, jwks: dict[str, Any], token_response: dict[str, Any]) -> None:
        self._jwks = jwks
        self._token_response = token_response
        self.jwks_fetches = 0
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
        if url.endswith("/jwks"):
            self.jwks_fetches += 1
            return dict(self._jwks)
        raise AssertionError(f"unexpected GET {url}")

    def post_form(
        self, url: str, *, data: dict[str, str], auth: Any, timeout: float
    ) -> dict[str, object]:
        return dict(self._token_response)


def _b64(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()


def _claims(**over: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "subject-abc-123",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": "the-nonce",
    }
    claims.update(over)
    return claims


def _sign(keypair: Any, claims: dict[str, Any], *, alg: str = "RS256") -> str:
    return jwt.encode(claims, keypair, algorithm=alg, headers={"kid": KID})


def _client(
    transport: FakeTransport, store: InMemoryStateStore | None = None
) -> OIDCClient:
    return OIDCClient(
        RelyingPartyConfig(
            provider_binding="corp-idp",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret="shh",
            redirect_uri=REDIRECT,
        ),
        state_store=store if store is not None else InMemoryStateStore(),
        transport=transport,
    )


def _peek_nonce(store: InMemoryStateStore, state_id: str) -> str:
    """Read the stored ceremony's nonce WITHOUT consuming it.

    A test standing in for the identity provider needs the nonce to mint a
    matching token; the real provider gets it from the authorization request.
    Deliberately not `take` — consuming here would make the subsequent
    `complete_login` fail for the wrong reason.
    """
    return store._held[state_id][1].nonce


def _validate(client: OIDCClient, token: str) -> dict[str, Any]:
    return client._validate_id_token(token)


# ── The happy path, so the refusals below mean something ────────────────────


def test_a_correctly_signed_token_validates(keypair: Any, jwks: dict[str, Any]) -> None:
    client = _client(FakeTransport(jwks, {}))
    claims = _validate(client, _sign(keypair, _claims()))
    assert claims["sub"] == "subject-abc-123"


@pytest.mark.parametrize("algorithm", ["PS256", "ES256"])
def test_the_confidential_client_retains_each_asymmetric_algorithm_family(
    algorithm: str,
) -> None:
    """The shared security core must not narrow the existing web contract.

    Public-native clients are RS256-only, but the confidential surface already
    publishes RSA-PSS and ECDSA support. Moving validation into one private core
    must preserve those families.
    """
    if algorithm == "ES256":
        signing_key = ec.generate_private_key(ec.SECP256R1())
        public_jwk = json.loads(
            jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key())
        )
    else:
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key())
        )
    public_jwk.update({"kid": KID, "use": "sig", "alg": algorithm})
    token = jwt.encode(
        _claims(), signing_key, algorithm=algorithm, headers={"kid": KID}
    )

    claims = _validate(_client(FakeTransport({"keys": [public_jwk]}, {})), token)

    assert claims["sub"] == "subject-abc-123"


# ── The algorithm allowlist ─────────────────────────────────────────────────


def test_an_hs256_token_forged_with_the_public_key_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """THE classic attack. The provider's public key is published, so it is not
    a secret; an HMAC-accepting validator lets anyone mint a valid token.

    Assembled BY HAND rather than with `jwt.encode`, because PyJWT >= 2.13
    refuses to encode HS256 with asymmetric key material — one of the
    protections the version floor exists for. An attacker has no such
    scruples, so the test must not either: encoding through the library would
    only prove PyJWT declines to help, not that this package refuses the token.
    """
    public_pem = (
        keypair.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    header = _b64({"alg": "HS256", "typ": "JWT", "kid": KID})
    payload = _b64(_claims())
    signing_input = f"{header}.{payload}".encode()
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(public_pem.encode(), signing_input, hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    forged = f"{header}.{payload}.{signature}"
    client = _client(FakeTransport(jwks, {}))

    with pytest.raises(UnsupportedAlgorithmError):
        _validate(client, forged)


def test_an_alg_none_token_is_refused(jwks: dict[str, Any]) -> None:
    unsigned = jwt.encode(_claims(), key=None, algorithm="none", headers={"kid": KID})
    client = _client(FakeTransport(jwks, {}))

    with pytest.raises(UnsupportedAlgorithmError):
        _validate(client, unsigned)


def test_the_allowlist_is_checked_before_the_key_is_fetched(
    jwks: dict[str, Any],
) -> None:
    """Ordering, not just outcome: a rejected algorithm must not cost a JWKS
    fetch, or an attacker drives provider traffic with junk tokens."""
    transport = FakeTransport(jwks, {})
    client = _client(transport)
    unsigned = jwt.encode(_claims(), key=None, algorithm="none", headers={"kid": KID})

    with pytest.raises(UnsupportedAlgorithmError):
        _validate(client, unsigned)
    assert transport.jwks_fetches == 0


# ── Key selection ───────────────────────────────────────────────────────────


def test_a_token_without_a_kid_is_refused(keypair: Any, jwks: dict[str, Any]) -> None:
    """ "Try every published key until one verifies" is not validation — it
    lets a token signed by any key the provider publishes for any purpose
    authenticate here."""
    token = jwt.encode(_claims(), keypair, algorithm="RS256")
    client = _client(FakeTransport(jwks, {}))

    with pytest.raises(IDTokenError, match="kid"):
        _validate(client, token)


def test_a_token_naming_an_unknown_key_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    token = jwt.encode(
        _claims(), keypair, algorithm="RS256", headers={"kid": "not-published"}
    )
    client = _client(FakeTransport(jwks, {}))

    with pytest.raises(Exception, match="kid"):
        _validate(client, token)


def test_a_tampered_payload_fails_the_signature(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    import base64

    token = _sign(keypair, _claims())
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["sub"] = "somebody-else"
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
    )
    client = _client(FakeTransport(jwks, {}))

    with pytest.raises(IDTokenError):
        _validate(client, f"{header}.{tampered_payload}.{signature}")


# ── Claims ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"iss": "https://evil.example.com"}, "wrong issuer"),
        ({"aud": "another-client"}, "wrong audience"),
        ({"exp": int(time.time()) - 3600}, "expired"),
    ],
)
def test_a_bad_claim_is_refused(
    keypair: Any, jwks: dict[str, Any], override: dict[str, Any], reason: str
) -> None:
    client = _client(FakeTransport(jwks, {}))
    with pytest.raises(IDTokenError):
        _validate(client, _sign(keypair, _claims(**override)))


@pytest.mark.parametrize("missing", ["sub", "exp", "iat", "aud", "iss"])
def test_a_missing_required_claim_is_refused(
    keypair: Any, jwks: dict[str, Any], missing: str
) -> None:
    claims = _claims()
    del claims[missing]
    client = _client(FakeTransport(jwks, {}))
    with pytest.raises(IDTokenError):
        _validate(client, _sign(keypair, claims))


def test_a_slightly_fast_provider_clock_is_tolerated(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """ERP has no leeway at all, so a provider one second ahead rejects every
    login. This is the regression guard for that fix."""
    client = _client(FakeTransport(jwks, {}))
    claims = _validate(client, _sign(keypair, _claims(iat=int(time.time()) + 30)))
    assert claims["sub"] == "subject-abc-123"


def test_a_clock_far_outside_the_leeway_is_still_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """The complement — leeway must be tolerance, not a hole."""
    client = _client(FakeTransport(jwks, {}))
    with pytest.raises(IDTokenError):
        _validate(client, _sign(keypair, _claims(exp=int(time.time()) - 600)))


def test_a_multi_audience_token_needs_azp_naming_this_client(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """ERP never reads `azp`. Without it, a token minted for a DIFFERENT relying
    party that merely lists us among its audiences is accepted here."""
    client = _client(FakeTransport(jwks, {}))
    claims = _claims(aud=[CLIENT_ID, "some-other-client"], azp="some-other-client")

    with pytest.raises(IDTokenError, match="azp"):
        _validate(client, _sign(keypair, claims))


def test_a_multi_audience_token_is_accepted_when_azp_is_this_client(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    client = _client(FakeTransport(jwks, {}))
    claims = _claims(aud=[CLIENT_ID, "some-other-client"], azp=CLIENT_ID)
    assert _validate(client, _sign(keypair, claims))["sub"] == "subject-abc-123"


# ── The full callback ───────────────────────────────────────────────────────


def test_complete_login_returns_the_verified_subject(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    transport = FakeTransport(jwks, {})
    store = InMemoryStateStore()
    client = _client(transport, store)
    redirect = client.start_login(return_to="/dashboard")
    nonce = _peek_nonce(store, redirect.state)
    transport._token_response = {"id_token": _sign(keypair, _claims(nonce=nonce))}

    subject = client.complete_login(
        code="an-authorization-code",
        state_parameter=redirect.state,
        stored_state=redirect.state,
    )

    assert subject.issuer == ISSUER
    assert subject.subject == "subject-abc-123"
    # Recovered from the STORED ceremony, not from anything the browser or the
    # provider could influence.
    assert subject.return_to == "/dashboard"


def test_the_authorization_url_carries_no_ceremony_secret(jwks: dict[str, Any]) -> None:
    """The front channel must expose the challenge and the opaque state, and
    nothing that would let an interceptor complete the exchange."""
    store = InMemoryStateStore()
    client = _client(FakeTransport(jwks, {}), store)
    redirect = client.start_login(return_to="/private/place")

    held = store._held[redirect.state][1]
    assert held.code_verifier not in redirect.url
    assert "/private/place" not in redirect.url
    assert "code_challenge_method=S256" in redirect.url
    assert f"state={redirect.state}" in redirect.url


def test_a_replayed_callback_is_refused(keypair: Any, jwks: dict[str, Any]) -> None:
    """The defect ERP has: its state stays valid for the whole TTL, so a
    captured callback URL can be completed twice. Here the first completion
    consumes the ceremony, so the second has no verifier to exchange with."""
    transport = FakeTransport(jwks, {})
    store = InMemoryStateStore()
    client = _client(transport, store)
    redirect = client.start_login()
    nonce = _peek_nonce(store, redirect.state)
    transport._token_response = {"id_token": _sign(keypair, _claims(nonce=nonce))}

    client.complete_login(
        code="code", state_parameter=redirect.state, stored_state=redirect.state
    )

    with pytest.raises(StateUnavailableError):
        client.complete_login(
            code="code", state_parameter=redirect.state, stored_state=redirect.state
        )


def test_a_token_from_a_different_ceremony_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """A genuine, correctly-signed token that belongs to another login. The
    nonce is the only thing that catches it."""
    transport = FakeTransport(jwks, {})
    client = _client(transport)
    redirect = client.start_login()
    transport._token_response = {
        "id_token": _sign(keypair, _claims(nonce="a-different-logins-nonce"))
    }

    with pytest.raises(NonceMismatchError):
        client.complete_login(
            code="code",
            state_parameter=redirect.state,
            stored_state=redirect.state,
        )


def test_a_callback_whose_state_does_not_match_the_cookie_is_refused(
    jwks: dict[str, Any],
) -> None:
    """Login CSRF: an attacker gets a victim's browser to complete the
    ATTACKER's login, silently binding the victim's session to the attacker's
    identity. Checking only one of the two states leaves this open."""
    client = _client(FakeTransport(jwks, {}))
    mine = client.start_login()
    attackers = client.start_login()

    with pytest.raises(StateError):
        client.complete_login(
            code="code",
            state_parameter=attackers.state,
            stored_state=mine.state,
        )


def test_a_token_response_without_an_id_token_is_refused(jwks: dict[str, Any]) -> None:
    transport = FakeTransport(jwks, {"access_token": "only-oauth2"})
    client = _client(transport)
    redirect = client.start_login()

    with pytest.raises(TokenExchangeError, match="id_token"):
        client.complete_login(
            code="code",
            state_parameter=redirect.state,
            stored_state=redirect.state,
        )


# ── Configuration hardening ─────────────────────────────────────────────────


def test_a_plain_http_discovery_override_is_refused() -> None:
    """The override bootstraps every other fetch — the token endpoint and the
    key set both come from the document it names — so it gets the same https
    rule the discovered endpoints get."""
    from dotmac_auth_oidc import ConfigurationError

    with pytest.raises(ConfigurationError, match="https"):
        RelyingPartyConfig(
            provider_binding="corp-idp",
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret="shh",
            redirect_uri=REDIRECT,
            discovery_url="http://idp.example.com/.well-known/openid-configuration",
        )


def test_an_https_discovery_override_is_accepted() -> None:
    config = RelyingPartyConfig(
        provider_binding="corp-idp",
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="shh",
        redirect_uri=REDIRECT,
        discovery_url=f"{ISSUER}/custom/openid-configuration",
    )
    assert config.discovery_url is not None
