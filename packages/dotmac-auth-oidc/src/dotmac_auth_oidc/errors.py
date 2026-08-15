"""The refusal taxonomy.

Every failure in this package is an `OIDCError`. The SUBCLASS NAME is the stable
reason code a caller may log, count or branch on — the same contract
`dotmac_kernel.licensing` uses for licence failures, and for the same reason: a
caller needs to distinguish "the provider is down" from "this token was forged"
without parsing a message string.

Nothing here is an HTTP exception. This package has no idea whether its consumer
is a JSON API, a rendered portal or a CLI, and a shared library that raised
`fastapi.HTTPException` would have decided that — ERP's client does exactly this
and it is one of the couplings that stops it being reusable.
"""

from __future__ import annotations


class OIDCError(Exception):
    """Base class for every refusal this package makes."""


# ── Configuration and discovery ─────────────────────────────────────────────


class ConfigurationError(OIDCError):
    """The relying-party configuration is unusable — missing issuer, blank
    client id, a redirect URI that is not absolute."""


class DiscoveryError(OIDCError):
    """The provider's discovery document could not be fetched or is unusable."""


class IssuerMismatchError(DiscoveryError):
    """The discovery document's `issuer` is not the issuer we asked for.

    A separate class because it is the one discovery failure that is an ATTACK
    rather than an outage: it means the document served at a well-known URL
    claims to speak for a different issuer.
    """


class JWKSError(OIDCError):
    """The provider's key set could not be fetched, or contains no usable key
    for the `kid` a token names."""


# ── The ceremony ────────────────────────────────────────────────────────────


class StateError(OIDCError):
    """The `state` round-trip failed: missing, malformed, tampered with,
    expired, or not the state this callback's cookie was issued for."""


class StateUnavailableError(StateError):
    """The store had no ceremony for this state id.

    Deliberately NEUTRAL about why, because the store cannot tell: an id that
    was already claimed, one that expired out of the store, and one this
    deployment never issued are the same absence. It was briefly named
    `StateReplayError`, which asserted evidence the store does not possess —
    and since the subclass NAME is a stable reason code a caller may log,
    count or alert on, that would have produced confident "replay attack"
    signals for what is usually a user pressing back or a slow login.

    The indistinguishability is also deliberate downstream: telling the three
    apart would let an attacker probe whether a given state id was ever real.
    """


class TokenExchangeError(OIDCError):
    """The authorization code could not be exchanged at the token endpoint."""


class IDTokenError(OIDCError):
    """The ID token failed validation: signature, algorithm, issuer, audience,
    expiry, or a claim that did not match the ceremony."""


class UnsupportedAlgorithmError(IDTokenError):
    """The token's `alg` is not in the allowlist.

    Its own class because this is the classic attack, not a compatibility
    problem: a token signed `HS256` using the provider's public RSA modulus as
    the HMAC key verifies perfectly against a naive validator. The allowlist is
    asymmetric-only and there is no configuration knob to widen it.
    """


class NonceMismatchError(IDTokenError):
    """The ID token's `nonce` is not the one this ceremony generated — the token
    is genuine but belongs to a different login."""


__all__ = [
    "ConfigurationError",
    "DiscoveryError",
    "IDTokenError",
    "IssuerMismatchError",
    "JWKSError",
    "NonceMismatchError",
    "OIDCError",
    "StateError",
    "StateUnavailableError",
    "TokenExchangeError",
    "UnsupportedAlgorithmError",
]
