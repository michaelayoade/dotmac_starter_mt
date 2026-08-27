"""Server-side verification for a PUBLIC native OIDC client.

These are the protocol tests ported from Sub's first mobile federation seam.
The device performs Authorization Code + PKCE itself; the backend receives one
ID token and recovers a trusted nonce binding from its own ceremony. There is
deliberately no code exchange, client secret, cookie or StateStore on this
surface.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from dotmac_auth_oidc import (
    IDTokenError,
    NativeIDTokenVerifier,
    NonceBinding,
    NonceMismatchError,
    PublicNativeClientConfig,
    UnsupportedAlgorithmError,
)

ISSUER = "https://idp.example.com/realms/mobile"
CLIENT_ID = "io.example.field"
KID = "native-key-1"
NONCE = "ceremony-nonce"


def _nonce_binding(nonce: str = NONCE) -> NonceBinding:
    return NonceBinding.from_plaintext(nonce)


@pytest.fixture(scope="module")
def keypair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def public_jwk(keypair: Any) -> dict[str, Any]:
    value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(keypair.public_key()))
    value.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return value


class NativeTransport:
    def __init__(self, public_jwk: dict[str, Any]) -> None:
        self.public_jwk = public_jwk
        self.gets = 0
        self.posts = 0

    def get_json(self, url: str, *, timeout: float) -> dict[str, object]:
        self.gets += 1
        if url.endswith("/.well-known/openid-configuration"):
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/certs",
            }
        if url.endswith("/certs"):
            return {"keys": [dict(self.public_jwk)]}
        raise AssertionError(f"unexpected GET {url}")

    def post_form(self, url: str, **kwargs: object) -> dict[str, object]:
        self.posts += 1
        raise AssertionError("the native verifier must never exchange a code")


def _config(**overrides: Any) -> PublicNativeClientConfig:
    values: dict[str, Any] = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "max_token_age_seconds": 300,
        "leeway_seconds": 60,
    }
    values.update(overrides)
    return PublicNativeClientConfig(**values)


def _verifier(transport: NativeTransport) -> NativeIDTokenVerifier:
    return NativeIDTokenVerifier(_config(), transport=transport)


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    values: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "external-subject",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": NONCE,
    }
    values.update(overrides)
    return values


def _sign(keypair: Any, claims: dict[str, Any], *, algorithm: str = "RS256") -> str:
    return jwt.encode(claims, keypair, algorithm=algorithm, headers={"kid": KID})


def _unsigned(claims: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "kid": KID}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}."


def test_a_native_assertion_returns_only_a_verified_subject(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    transport = NativeTransport(public_jwk)
    subject = _verifier(transport).verify(
        _sign(keypair, _claims()), nonce_binding=_nonce_binding()
    )

    assert subject.issuer == ISSUER
    assert subject.subject == "external-subject"
    assert subject.claims["nonce"] == NONCE
    assert transport.posts == 0


def test_a_static_jwks_uri_needs_no_discovery_fetch(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    transport = NativeTransport(public_jwk)
    verifier = NativeIDTokenVerifier(
        _config(jwks_uri=f"{ISSUER}/certs"),
        transport=transport,
    )

    subject = verifier.verify(_sign(keypair, _claims()), nonce_binding=_nonce_binding())

    assert subject.subject == "external-subject"
    assert transport.gets == 1


def test_the_public_native_policy_is_rs256_only() -> None:
    from dotmac_auth_oidc import NATIVE_ID_TOKEN_ALGORITHMS

    assert NATIVE_ID_TOKEN_ALGORITHMS == frozenset({"RS256"})


def test_an_unsigned_assertion_is_refused_before_any_fetch(
    public_jwk: dict[str, Any],
) -> None:
    transport = NativeTransport(public_jwk)
    with pytest.raises(UnsupportedAlgorithmError):
        _verifier(transport).verify(
            _unsigned(_claims()), nonce_binding=_nonce_binding()
        )
    assert transport.gets == 0


@pytest.mark.parametrize(
    "jwk_override",
    [
        {"use": "enc"},
        {"key_ops": ["encrypt"]},
        {"alg": "RS512"},
    ],
)
def test_a_key_not_declared_for_this_signature_is_refused(
    keypair: Any,
    public_jwk: dict[str, Any],
    jwk_override: dict[str, Any],
) -> None:
    incompatible = {**public_jwk, **jwk_override}
    with pytest.raises(IDTokenError):
        _verifier(NativeTransport(incompatible)).verify(
            _sign(keypair, _claims()), nonce_binding=_nonce_binding()
        )


@pytest.mark.parametrize(
    "claims",
    [
        _claims(iss="https://issuer.example.invalid"),
        _claims(aud="another-client"),
    ],
)
def test_the_issuer_and_audience_are_exact(
    keypair: Any, public_jwk: dict[str, Any], claims: dict[str, Any]
) -> None:
    with pytest.raises(IDTokenError):
        _verifier(NativeTransport(public_jwk)).verify(
            _sign(keypair, claims), nonce_binding=_nonce_binding()
        )


def test_a_configured_client_id_is_the_only_accepted_audience(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    verifier = NativeIDTokenVerifier(
        _config(client_id="io.example.another"),
        transport=NativeTransport(public_jwk),
    )
    with pytest.raises(IDTokenError):
        verifier.verify(_sign(keypair, _claims()), nonce_binding=_nonce_binding())


def test_multiple_audiences_require_this_client_as_azp(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    token = _sign(
        keypair,
        _claims(aud=[CLIENT_ID, "another-client"], azp="another-client"),
    )
    with pytest.raises(IDTokenError, match="azp"):
        _verifier(NativeTransport(public_jwk)).verify(
            token, nonce_binding=_nonce_binding()
        )


def test_multiple_audiences_accept_this_client_as_azp(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    token = _sign(
        keypair,
        _claims(aud=[CLIENT_ID, "another-client"], azp=CLIENT_ID),
    )
    subject = _verifier(NativeTransport(public_jwk)).verify(
        token, nonce_binding=_nonce_binding()
    )
    assert subject.subject == "external-subject"


@pytest.mark.parametrize("missing", ["iss", "sub", "aud", "exp", "iat"])
def test_security_claims_are_required(
    keypair: Any, public_jwk: dict[str, Any], missing: str
) -> None:
    claims = _claims()
    del claims[missing]
    with pytest.raises(IDTokenError):
        _verifier(NativeTransport(public_jwk)).verify(
            _sign(keypair, claims), nonce_binding=_nonce_binding()
        )


def test_nbf_is_enforced_when_present(keypair: Any, public_jwk: dict[str, Any]) -> None:
    now = int(time.time())
    token = _sign(keypair, _claims(iat=now + 3600, nbf=now + 3600, exp=now + 7200))
    with pytest.raises(IDTokenError):
        _verifier(NativeTransport(public_jwk)).verify(
            token, nonce_binding=_nonce_binding()
        )


def test_an_unexpired_but_old_assertion_is_refused(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    now = int(time.time())
    token = _sign(keypair, _claims(iat=now - 7200, exp=now + 7200))
    with pytest.raises(IDTokenError, match="maximum age"):
        _verifier(NativeTransport(public_jwk)).verify(
            token, nonce_binding=_nonce_binding()
        )


@pytest.mark.parametrize("claim_nonce", [None, "", "another-ceremony"])
def test_the_nonce_is_required_and_bound_to_the_ceremony(
    keypair: Any, public_jwk: dict[str, Any], claim_nonce: str | None
) -> None:
    claims = _claims()
    if claim_nonce is None:
        del claims["nonce"]
    else:
        claims["nonce"] = claim_nonce
    with pytest.raises(NonceMismatchError):
        _verifier(NativeTransport(public_jwk)).verify(
            _sign(keypair, claims), nonce_binding=_nonce_binding()
        )


def test_a_persisted_nonce_digest_is_accepted(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    digest = hashlib.sha256(NONCE.encode()).hexdigest()
    subject = _verifier(NativeTransport(public_jwk)).verify(
        _sign(keypair, _claims()),
        nonce_binding=NonceBinding.from_sha256_hex(digest),
    )
    assert subject.subject == "external-subject"


def test_a_wrong_persisted_nonce_digest_is_refused(
    keypair: Any, public_jwk: dict[str, Any]
) -> None:
    wrong = hashlib.sha256(b"another-ceremony").hexdigest()
    with pytest.raises(NonceMismatchError):
        _verifier(NativeTransport(public_jwk)).verify(
            _sign(keypair, _claims()),
            nonce_binding=NonceBinding.from_sha256_hex(wrong),
        )


@pytest.mark.parametrize("invalid", ["", "abc", "A" * 64, "g" * 64])
def test_a_malformed_persisted_nonce_digest_is_refused(invalid: str) -> None:
    from dotmac_auth_oidc import ConfigurationError

    with pytest.raises(ConfigurationError, match="64 lowercase"):
        NonceBinding.from_sha256_hex(invalid)


def test_an_empty_plaintext_nonce_binding_is_refused() -> None:
    from dotmac_auth_oidc import ConfigurationError

    with pytest.raises(ConfigurationError, match="cannot be empty"):
        NonceBinding.from_plaintext("")


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "http://idp.example.com"},
        {"client_id": ""},
        {"max_token_age_seconds": 0},
        {"leeway_seconds": -1},
        {
            "discovery_url": f"{ISSUER}/custom-discovery",
            "jwks_uri": f"{ISSUER}/certs",
        },
    ],
)
def test_an_unsafe_or_ambiguous_native_config_is_refused(
    overrides: dict[str, Any],
) -> None:
    from dotmac_auth_oidc import ConfigurationError

    with pytest.raises(ConfigurationError):
        _config(**overrides)
