"""Provider discovery and the JWKS, both cached.

ERP refetches the discovery document TWICE per callback and the key set on every
single login (`app/services/sso/oidc.py:260-267` — `httpx.get(metadata.jwks_uri)`
inline inside token validation). That is the gap this module closes: it makes
every login depend on the provider's availability and latency, and it turns a
provider outage into a total login outage rather than a degraded one.

Caching a key set is not merely an optimization, though. It has a security
consequence in both directions, and the rules below are what keep it honest:

- a cached key set must EXPIRE, or a rotated-out key stays trusted forever;
- a cache miss on an unknown `kid` must be able to force ONE refetch, or key
  rotation breaks every login until the TTL lapses;
- that forced refetch must be RATE-LIMITED, or an attacker mints tokens with
  random `kid` values and turns the relying party into a request amplifier
  pointed at the provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from dotmac_auth_oidc.errors import DiscoveryError, IssuerMismatchError, JWKSError
from dotmac_auth_oidc.transport import DEFAULT_TIMEOUT_SECONDS, Transport

DEFAULT_DISCOVERY_TTL_SECONDS = 3600.0
DEFAULT_JWKS_TTL_SECONDS = 3600.0

# The floor between two forced JWKS refetches for an unknown `kid`. Without it,
# a token carrying a random `kid` costs the provider one request every time.
DEFAULT_JWKS_MIN_REFETCH_SECONDS = 60.0

_WELL_KNOWN = "/.well-known/openid-configuration"


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """The three endpoints this package uses, plus the verified issuer."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def discovery_url(issuer: str) -> str:
    return issuer.rstrip("/") + _WELL_KNOWN


def require_https_url(url: object, *, field: str, error: type[Exception]) -> str:
    """PARSED validation: an https scheme and a real hostname.

    A `startswith("https://")` prefix check is not enough, and the gap is not
    theoretical: `"https://"` alone passes it, as does `"https:///path"` — both
    have no host, so the request would go nowhere useful while having satisfied
    what looked like a TLS guarantee. `urlsplit` is what distinguishes a URL
    from a string that begins like one.

    The rule itself: an `http://` token endpoint would carry the client secret
    in clear text, and an `http://` JWKS URL means the key set that decides
    identity is whatever the network says it is. There is no override — a
    provider that cannot do TLS cannot be an identity provider, and
    `http://localhost` is not special-cased (a developer pointing at a local IdP
    terminates TLS or injects a fake transport).
    """
    if not isinstance(url, str) or not url.strip():
        raise error(f"{field} is missing")
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise error(
            f"{field} must use https — refusing to send credentials or trust "
            f"keys over an unprotected transport (got {parts.scheme or 'no'} "
            "scheme)"
        )
    if not parts.hostname:
        raise error(f"{field} has no host: {url!r}")
    return url.strip()


def _require_https(url: object, *, field: str) -> str:
    return require_https_url(
        url, field=f"discovery document's {field!r}", error=DiscoveryError
    )


def fetch_metadata(
    issuer: str,
    *,
    transport: Transport,
    discovery_override: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProviderMetadata:
    """Fetch and validate the discovery document.

    The load-bearing check is the issuer match. A document is only trustworthy
    if it claims to speak for the issuer we asked about — otherwise a provider
    that can serve content at a well-known URL could redirect the ceremony's
    endpoints anywhere. ERP does check this (`oidc.py:115-119`) and it is ported
    verbatim in intent, including the trailing-slash normalisation.
    """
    url = discovery_override or discovery_url(issuer)
    document = transport.get_json(url, timeout=timeout)

    declared = document.get("issuer")
    if not isinstance(declared, str) or declared.rstrip("/") != issuer.rstrip("/"):
        raise IssuerMismatchError(
            f"discovery document at {url!r} declares issuer {declared!r}, "
            f"which is not {issuer!r}"
        )

    return ProviderMetadata(
        issuer=issuer.rstrip("/"),
        authorization_endpoint=_require_https(
            document.get("authorization_endpoint"), field="authorization_endpoint"
        ),
        token_endpoint=_require_https(
            document.get("token_endpoint"), field="token_endpoint"
        ),
        jwks_uri=_require_https(document.get("jwks_uri"), field="jwks_uri"),
    )


class ProviderCache:
    """Cached discovery metadata and key set for ONE issuer.

    Not thread-safe by lock, and deliberately so: the worst outcome of a race is
    two concurrent refetches storing equivalent documents. Serialising every
    login behind a mutex to avoid a duplicate HTTP call would be the wrong
    trade, and a wrong result is not among the outcomes.
    """

    __slots__ = (
        "_discovery_ttl",
        "_issuer",
        "_jwks",
        "_jwks_fetched_at",
        "_jwks_min_refetch",
        "_jwks_ttl",
        "_metadata",
        "_metadata_fetched_at",
        "_override",
        "_timeout",
        "_transport",
    )

    def __init__(
        self,
        issuer: str,
        *,
        transport: Transport,
        discovery_override: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        discovery_ttl: float = DEFAULT_DISCOVERY_TTL_SECONDS,
        jwks_ttl: float = DEFAULT_JWKS_TTL_SECONDS,
        jwks_min_refetch: float = DEFAULT_JWKS_MIN_REFETCH_SECONDS,
    ) -> None:
        self._issuer = issuer
        self._transport = transport
        self._override = discovery_override
        self._timeout = timeout
        self._discovery_ttl = discovery_ttl
        self._jwks_ttl = jwks_ttl
        self._jwks_min_refetch = jwks_min_refetch
        self._metadata: ProviderMetadata | None = None
        self._metadata_fetched_at = 0.0
        self._jwks: dict[str, dict[str, Any]] = {}
        self._jwks_fetched_at = 0.0

    @property
    def transport(self) -> Transport:
        """The transport this cache was built with.

        Public so the client can reach the token endpoint through the SAME
        transport as discovery and JWKS — one injected object for every
        outbound request, rather than the client quietly opening its own.
        """
        return self._transport

    def metadata(self, *, now: float | None = None) -> ProviderMetadata:
        clock = time.monotonic() if now is None else now
        cached = self._metadata
        age = clock - self._metadata_fetched_at
        if cached is not None and age < self._discovery_ttl:
            return cached
        fetched = fetch_metadata(
            self._issuer,
            transport=self._transport,
            discovery_override=self._override,
            timeout=self._timeout,
        )
        self._metadata = fetched
        self._metadata_fetched_at = clock
        return fetched

    def _refresh_jwks(self, *, clock: float) -> None:
        metadata = self.metadata(now=clock)
        document = self._transport.get_json(metadata.jwks_uri, timeout=self._timeout)
        keys = document.get("keys")
        if not isinstance(keys, list) or not keys:
            raise JWKSError(f"key set at {metadata.jwks_uri!r} contains no keys")

        by_kid: dict[str, dict[str, Any]] = {}
        for key in keys:
            if not isinstance(key, dict):
                continue
            kid = key.get("kid")
            # A key with no `kid` is unaddressable: a token names its key by
            # `kid`, so a keyless entry could only ever be selected by guessing,
            # and "try every key until one verifies" is how an unrelated key
            # ends up validating a token.
            if isinstance(kid, str) and kid:
                by_kid[kid] = key

        if not by_kid:
            raise JWKSError(
                f"key set at {metadata.jwks_uri!r} has no key carrying a `kid`"
            )
        self._jwks = by_kid
        self._jwks_fetched_at = clock

    def signing_key(self, kid: str, *, now: float | None = None) -> dict[str, Any]:
        """The JWK for `kid`, refetching once if it is unknown.

        The rotation path: a provider signs with a new key, we have never seen
        its `kid`, and one forced refetch picks it up. The amplification guard:
        that refetch is refused if the last one was under `jwks_min_refetch`
        seconds ago, so a stream of tokens carrying random `kid` values costs
        one upstream request per interval rather than one per token.
        """
        clock = time.monotonic() if now is None else now
        stale = clock - self._jwks_fetched_at >= self._jwks_ttl

        if not self._jwks or stale:
            self._refresh_jwks(clock=clock)

        key = self._jwks.get(kid)
        if key is not None:
            return key

        if clock - self._jwks_fetched_at >= self._jwks_min_refetch:
            self._refresh_jwks(clock=clock)
            key = self._jwks.get(kid)
            if key is not None:
                return key

        raise JWKSError(
            f"no key with kid {kid!r} in the provider's key set — the token "
            "names a signing key this provider does not publish"
        )


__all__ = [
    "DEFAULT_DISCOVERY_TTL_SECONDS",
    "DEFAULT_JWKS_MIN_REFETCH_SECONDS",
    "DEFAULT_JWKS_TTL_SECONDS",
    "ProviderCache",
    "ProviderMetadata",
    "discovery_url",
    "fetch_metadata",
    "require_https_url",
]
