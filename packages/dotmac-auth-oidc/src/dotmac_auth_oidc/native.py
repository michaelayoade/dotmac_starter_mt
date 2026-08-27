"""Server-side verification of an ID token from a PUBLIC native client.

The native application owns Authorization Code + PKCE. A backend uses this
surface after receiving the resulting ID token and recovering its trusted nonce
binding from its own ceremony. It verifies the assertion and stops at an
external subject; local identity resolution, authorization and session issuance
remain product-owned.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from dotmac_auth_oidc._token import decode_verified_id_token
from dotmac_auth_oidc.client import DEFAULT_LEEWAY_SECONDS, VerifiedSubject
from dotmac_auth_oidc.discovery import (
    DEFAULT_DISCOVERY_TTL_SECONDS,
    DEFAULT_JWKS_MIN_REFETCH_SECONDS,
    DEFAULT_JWKS_TTL_SECONDS,
    ProviderCache,
    require_https_url,
)
from dotmac_auth_oidc.errors import (
    ConfigurationError,
    IDTokenError,
    NonceMismatchError,
)
from dotmac_auth_oidc.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpxTransport,
    Transport,
)

# The first native contract is exact: the authorization-server client is
# pinned to RS256, so accepting another family here would widen the trust
# boundary without a consumer that needs it. This is intentionally not a
# constructor argument.
NATIVE_ID_TOKEN_ALGORITHMS: frozenset[str] = frozenset({"RS256"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True, repr=False)
class NonceBinding:
    """A trusted SHA-256 binding to the nonce a backend ceremony issued.

    The object never retains raw nonce material. A product may construct it
    while it still has the plaintext, or restore it from a digest persisted by
    its own ceremony owner.
    """

    sha256_hex: str

    def __post_init__(self) -> None:
        if not isinstance(self.sha256_hex, str) or not _SHA256_HEX.fullmatch(
            self.sha256_hex
        ):
            raise ConfigurationError(
                "nonce binding must be exactly 64 lowercase SHA-256 hex characters"
            )

    @classmethod
    def from_plaintext(cls, nonce: str) -> NonceBinding:
        if not isinstance(nonce, str) or not nonce:
            raise ConfigurationError("a plaintext nonce binding cannot be empty")
        return cls(hashlib.sha256(nonce.encode("utf-8")).hexdigest())

    @classmethod
    def from_sha256_hex(cls, digest: str) -> NonceBinding:
        return cls(digest)

    def matches(self, nonce: str) -> bool:
        candidate = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        return secrets.compare_digest(candidate, self.sha256_hex)

    def __repr__(self) -> str:
        return "NonceBinding(sha256=<held>)"


@dataclass(frozen=True, slots=True)
class PublicNativeClientConfig:
    """Trusted server-side registration data for one public native client."""

    issuer: str
    client_id: str
    max_token_age_seconds: int
    leeway_seconds: int = DEFAULT_LEEWAY_SECONDS
    discovery_url: str | None = None
    jwks_uri: str | None = None

    def __post_init__(self) -> None:
        issuer = require_https_url(
            self.issuer, field="issuer", error=ConfigurationError
        ).rstrip("/")
        object.__setattr__(self, "issuer", issuer)
        for name in ("client_id",):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"public-native client config needs a non-empty {name!r}"
                )
            object.__setattr__(self, name, value.strip())
        for name, minimum in (
            ("max_token_age_seconds", 1),
            ("leeway_seconds", 0),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ConfigurationError(f"{name} must be an integer >= {minimum}")
        if self.discovery_url is not None:
            require_https_url(
                self.discovery_url,
                field="discovery_url override",
                error=ConfigurationError,
            )
        if self.jwks_uri is not None:
            require_https_url(
                self.jwks_uri,
                field="jwks_uri override",
                error=ConfigurationError,
            )
        if self.discovery_url is not None and self.jwks_uri is not None:
            raise ConfigurationError(
                "configure either a discovery override or a static jwks_uri, not both"
            )


class NativeIDTokenVerifier:
    """Long-lived verifier for one public native-client registration.

    Build one per registration and retain it for the process lifetime so its
    ProviderCache survives requests and key rotation remains bounded.
    """

    __slots__ = ("_cache", "_config")

    def __init__(
        self,
        config: PublicNativeClientConfig,
        *,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        discovery_ttl: float = DEFAULT_DISCOVERY_TTL_SECONDS,
        jwks_ttl: float = DEFAULT_JWKS_TTL_SECONDS,
        jwks_min_refetch: float = DEFAULT_JWKS_MIN_REFETCH_SECONDS,
    ) -> None:
        self._config = config
        self._cache = ProviderCache(
            config.issuer,
            transport=transport or HttpxTransport(),
            discovery_override=config.discovery_url,
            jwks_override=config.jwks_uri,
            timeout=timeout,
            discovery_ttl=discovery_ttl,
            jwks_ttl=jwks_ttl,
            jwks_min_refetch=jwks_min_refetch,
        )

    @property
    def config(self) -> PublicNativeClientConfig:
        return self._config

    def verify(self, id_token: str, *, nonce_binding: NonceBinding) -> VerifiedSubject:
        """Verify one assertion and return its opaque external subject."""
        if not isinstance(id_token, str) or not id_token:
            raise IDTokenError("ID token is missing")
        if not isinstance(nonce_binding, NonceBinding):
            raise ConfigurationError("verify requires a trusted NonceBinding")

        claims = decode_verified_id_token(
            id_token,
            cache=self._cache,
            issuer=self._config.issuer,
            audience=self._config.client_id,
            client_id=self._config.client_id,
            allowed_algorithms=NATIVE_ID_TOKEN_ALGORITHMS,
            leeway_seconds=self._config.leeway_seconds,
        )

        issued_at = claims["iat"]
        age = time.time() - issued_at
        if age > (self._config.max_token_age_seconds + self._config.leeway_seconds):
            raise IDTokenError("ID token is older than this client's maximum age")
        if age < -self._config.leeway_seconds:
            raise IDTokenError("ID token was issued in the future")

        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not nonce or not nonce_binding.matches(nonce):
            raise NonceMismatchError(
                "the ID token's nonce is not this backend ceremony's nonce"
            )

        return VerifiedSubject(
            issuer=self._config.issuer,
            subject=claims["sub"],
            claims=claims,
        )


__all__ = [
    "NATIVE_ID_TOKEN_ALGORITHMS",
    "NativeIDTokenVerifier",
    "NonceBinding",
    "PublicNativeClientConfig",
]
