"""DotMac server-side OIDC protocol adapter.

The SINGLE CANONICAL public-surface manifest, deliberately shaped like
`dotmac_kernel.__init__` and `dotmac_ui.__init__` so the packages are governed
the same way. The prose companion is `COMPATIBILITY.md`.

## What this package is

One entry point runs a confidential Authorization Code flow with PKCE. A second
verifies the ID token obtained by a public native client without taking over
that client's PKCE ceremony. Both hand back a **verified external subject**.
That is the whole output:

    dotmac-auth-oidc                 →  verified (issuer, subject)
    dotmac_kernel.external_identity  →  which local Party is that, here?
    the product's identity facet     →  issue ITS OWN session

## What it is NOT, and will not become

- **not a user store** — it queries no local table and creates no account;
- **not a session issuer** — it mints no token and sets no cookie;
- **not an authorization source** — provider roles, groups, scopes and
  organization claims are never read as permissions. Authorization is local
  (`dotmac_kernel.deps.authorize_party`);
- **not provider-aware** — there is no Keycloak, Entra, Google or Auth0 branch
  anywhere, and adding one is the wrong fix for a provider quirk. ADR-0024 is
  explicit that shared execution paths carry no product or provider
  conditionals;
- **not stateful** — no rows, so no `short_code`, no `migration_prefix`, no
  namespace allocation (hard rule 14).

STATELESS, so it declares no persistence plane at all. Its only state is the
in-process discovery/JWKS cache, which is rebuildable by definition.
"""

from __future__ import annotations

from typing import Final

from dotmac_auth_oidc.client import (
    ALLOWED_ALGORITHMS,
    DEFAULT_LEEWAY_SECONDS,
    DEFAULT_SCOPES,
    AuthorizationRedirect,
    OIDCClient,
    RelyingPartyConfig,
    VerifiedSubject,
)
from dotmac_auth_oidc.discovery import (
    ProviderCache,
    ProviderMetadata,
    discovery_url,
    fetch_metadata,
)
from dotmac_auth_oidc.errors import (
    ConfigurationError,
    DiscoveryError,
    IDTokenError,
    IssuerMismatchError,
    JWKSError,
    NonceMismatchError,
    OIDCError,
    StateError,
    StateUnavailableError,
    TokenExchangeError,
    UnsupportedAlgorithmError,
)
from dotmac_auth_oidc.native import (
    NATIVE_ID_TOKEN_ALGORITHMS,
    NativeIDTokenVerifier,
    NonceBinding,
    PublicNativeClientConfig,
)
from dotmac_auth_oidc.state import (
    DEFAULT_STATE_TTL_SECONDS,
    PER_REQUEST_STATE_STORE,
    InMemoryStateStore,
    LoginState,
    PerRequestStateStore,
    PKCEPair,
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

# Not read from `importlib.metadata`: the package must import from a bare source
# checkout, where installed metadata may be absent or stale. Kept in sync with
# pyproject by `tests/architecture/test_auth_oidc_public_surface.py`.
__version__: Final[str] = "0.1.0a2"

# The exhaustive list of submodules a consumer may import from. A name is public
# only if it is also in that module's own `__all__`.
SUPPORTED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "dotmac_auth_oidc.client",
        "dotmac_auth_oidc.discovery",
        "dotmac_auth_oidc.errors",
        "dotmac_auth_oidc.native",
        "dotmac_auth_oidc.state",
        "dotmac_auth_oidc.transport",
    }
)

INTERNAL_MODULES: Final[frozenset[str]] = frozenset({"dotmac_auth_oidc._token"})

__all__ = [
    "ALLOWED_ALGORITHMS",
    "DEFAULT_LEEWAY_SECONDS",
    "DEFAULT_SCOPES",
    "DEFAULT_STATE_TTL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "INTERNAL_MODULES",
    "NATIVE_ID_TOKEN_ALGORITHMS",
    "PER_REQUEST_STATE_STORE",
    "SUPPORTED_MODULES",
    "AuthorizationRedirect",
    "ConfigurationError",
    "DiscoveryError",
    "HttpxTransport",
    "IDTokenError",
    "InMemoryStateStore",
    "IssuerMismatchError",
    "JWKSError",
    "LoginState",
    "NativeIDTokenVerifier",
    "NonceBinding",
    "NonceMismatchError",
    "OIDCClient",
    "OIDCError",
    "PKCEPair",
    "PerRequestStateStore",
    "ProviderCache",
    "ProviderMetadata",
    "PublicNativeClientConfig",
    "RelyingPartyConfig",
    "StateError",
    "StateStore",
    "StateUnavailableError",
    "TokenExchangeError",
    "Transport",
    "UnsupportedAlgorithmError",
    "VerifiedSubject",
    "__version__",
    "claim_state",
    "discovery_url",
    "fetch_metadata",
    "generate_pkce",
    "generate_state_id",
]
