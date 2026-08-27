"""Shared ID-token signature and registered-claim verification.

Private on purpose. The confidential relying party and the server-side native
verifier expose different protocol roles, but a second signature/JWKS/claim
implementation would make one of them the copy that misses the next fix.
"""

from __future__ import annotations

from typing import Any

from dotmac_auth_oidc.discovery import ProviderCache
from dotmac_auth_oidc.errors import (
    IDTokenError,
    UnsupportedAlgorithmError,
)


def decode_verified_id_token(
    id_token: str,
    *,
    cache: ProviderCache,
    issuer: str,
    audience: str,
    client_id: str,
    allowed_algorithms: frozenset[str],
    leeway_seconds: int,
) -> dict[str, Any]:
    """Verify one ID token through the package's single security core."""
    import jwt

    try:
        header = jwt.get_unverified_header(id_token)
    except Exception as exc:
        raise IDTokenError(f"ID token header is unreadable: {exc}") from exc

    algorithm = header.get("alg")
    # Before key resolution: an unsupported algorithm must cost no outbound
    # request, especially for an unauthenticated public-native exchange.
    if not isinstance(algorithm, str) or algorithm not in allowed_algorithms:
        raise UnsupportedAlgorithmError(
            f"ID token algorithm {algorithm!r} is not permitted"
        )

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise IDTokenError(
            "ID token names no `kid` — the signing key is unaddressable, and "
            "trying every published key until one verifies is not validation"
        )

    jwk = cache.signing_key(kid)
    use = jwk.get("use")
    if use is not None and use != "sig":
        raise IDTokenError(f"provider key {kid!r} is not a signing key")
    key_ops = jwk.get("key_ops")
    if key_ops is not None and (
        not isinstance(key_ops, list)
        or not all(isinstance(operation, str) for operation in key_ops)
        or "verify" not in key_ops
    ):
        raise IDTokenError(f"provider key {kid!r} does not permit verification")
    declared_algorithm = jwk.get("alg")
    if declared_algorithm is not None and declared_algorithm != algorithm:
        raise IDTokenError(
            f"provider key {kid!r} declares algorithm {declared_algorithm!r}, "
            f"not the token's {algorithm!r}"
        )
    try:
        key = jwt.PyJWK(jwk)
    except Exception as exc:
        raise IDTokenError(f"provider key {kid!r} is unusable: {exc}") from exc

    try:
        claims = jwt.decode(
            id_token,
            # Keep the PyJWK wrapper: its kty/alg binding is a second check on
            # the token-selected algorithm. Unwrapping `.key` discards it.
            key=key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
            leeway=leeway_seconds,
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
    if claims.get("iss") != issuer:
        raise IDTokenError("ID token issuer does not exactly match this registration")

    claimed_audience = claims.get("aud")
    if isinstance(claimed_audience, str):
        audiences = [claimed_audience]
    elif isinstance(claimed_audience, list) and all(
        isinstance(item, str) and item for item in claimed_audience
    ):
        audiences = claimed_audience
    else:
        raise IDTokenError("ID token audience is not a string or string array")
    if audience not in audiences:
        raise IDTokenError("ID token audience does not name this registration")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise IDTokenError(
            "ID token has multiple audiences and its `azp` is not this client"
        )

    issued_at = claims.get("iat")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        raise IDTokenError("ID token `iat` is not an integer timestamp")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise IDTokenError("ID token carries no usable `sub`")
    return claims


__all__: list[str] = []
