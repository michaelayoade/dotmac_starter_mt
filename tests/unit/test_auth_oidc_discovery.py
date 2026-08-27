"""Discovery and JWKS: the issuer match, the https rule, and the caching that
ERP does not do.

ERP refetches the discovery document twice per callback and the key set on every
login, inline inside token validation. That makes every login depend on the
provider's latency and turns a provider blip into a total login outage. Caching
fixes it, and introduces its own three rules — expiry, rotation, and an
amplification guard — which are what most of this file pins.
"""

from __future__ import annotations

from typing import Any

import pytest
from dotmac_auth_oidc import (
    DiscoveryError,
    IssuerMismatchError,
    JWKSError,
    ProviderCache,
    discovery_url,
    fetch_metadata,
)

ISSUER = "https://idp.example.com"


class RecordingTransport:
    def __init__(
        self,
        *,
        document: dict[str, Any] | None = None,
        keys: list[dict[str, Any]] | None = None,
    ) -> None:
        self.document = document if document is not None else _document()
        self.keys = keys if keys is not None else [{"kid": "k1", "kty": "RSA"}]
        self.discovery_fetches = 0
        self.jwks_fetches = 0
        self.fail_jwks = False

    def get_json(self, url: str, *, timeout: float) -> dict[str, object]:
        if url.endswith("/.well-known/openid-configuration"):
            self.discovery_fetches += 1
            return dict(self.document)
        self.jwks_fetches += 1
        if self.fail_jwks:
            raise RuntimeError("provider unavailable")
        return {"keys": list(self.keys)}

    def post_form(self, url: str, **kwargs: Any) -> dict[str, object]:
        raise AssertionError("not used")


def _document(**over: Any) -> dict[str, Any]:
    doc = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }
    doc.update(over)
    return doc


# ── Discovery ───────────────────────────────────────────────────────────────


def test_the_well_known_url_is_derived_from_the_issuer() -> None:
    assert discovery_url(ISSUER) == f"{ISSUER}/.well-known/openid-configuration"
    assert discovery_url(ISSUER + "/") == f"{ISSUER}/.well-known/openid-configuration"


def test_metadata_is_read_from_the_document() -> None:
    metadata = fetch_metadata(ISSUER, transport=RecordingTransport())
    assert metadata.token_endpoint == f"{ISSUER}/token"
    assert metadata.jwks_uri == f"{ISSUER}/jwks"


def test_a_document_claiming_another_issuer_is_refused() -> None:
    """The one discovery failure that is an ATTACK rather than an outage: the
    document served at our well-known URL claims to speak for someone else, and
    accepting it would let it point the ceremony's endpoints anywhere."""
    transport = RecordingTransport(document=_document(issuer="https://evil.example"))
    with pytest.raises(IssuerMismatchError):
        fetch_metadata(ISSUER, transport=transport)


def test_a_trailing_slash_is_not_an_issuer_mismatch() -> None:
    transport = RecordingTransport(document=_document(issuer=ISSUER + "/"))
    assert fetch_metadata(ISSUER, transport=transport).issuer == ISSUER


@pytest.mark.parametrize(
    "field", ["authorization_endpoint", "token_endpoint", "jwks_uri"]
)
def test_a_plain_http_endpoint_is_refused(field: str) -> None:
    """An `http` token endpoint carries the client secret in clear text; an
    `http` JWKS means the key set that decides identity is whatever the network
    says it is. There is no override."""
    transport = RecordingTransport(
        document=_document(**{field: "http://idp.example.com/x"})
    )
    with pytest.raises(DiscoveryError, match="https"):
        fetch_metadata(ISSUER, transport=transport)


@pytest.mark.parametrize(
    "bad",
    [
        "https://",  # prefix-valid, no host at all
        "https:///path",  # prefix-valid, empty authority
        "HTTPS://idp.example.com",  # scheme case is normalised by urlsplit
        "//idp.example.com/x",  # scheme-relative, no scheme
        "  ",
    ],
)
def test_a_url_that_only_looks_https_is_refused(bad: str) -> None:
    """The gap a `startswith("https://")` check leaves open.

    `"https://"` and `"https:///path"` both pass a prefix test and have no host,
    so a request would go nowhere while appearing to carry a TLS guarantee.
    Parsing is what separates a URL from a string that begins like one.
    (`HTTPS://` is included as a control: it IS valid once parsed, so it must
    NOT raise — see the companion test below.)
    """
    from dotmac_auth_oidc.discovery import require_https_url

    if bad.lower().startswith("https://") and "idp.example.com" in bad:
        assert require_https_url(bad, field="x", error=DiscoveryError)
        return
    with pytest.raises(DiscoveryError):
        require_https_url(bad, field="x", error=DiscoveryError)


def test_a_well_formed_https_url_passes() -> None:
    from dotmac_auth_oidc.discovery import require_https_url

    assert (
        require_https_url(f"  {ISSUER}/token  ", field="x", error=DiscoveryError)
        == f"{ISSUER}/token"
    )


@pytest.mark.parametrize(
    "field", ["authorization_endpoint", "token_endpoint", "jwks_uri"]
)
def test_a_hostless_endpoint_in_the_document_is_refused(field: str) -> None:
    """The same gap, reached through discovery rather than through config."""
    transport = RecordingTransport(document=_document(**{field: "https://"}))
    with pytest.raises(DiscoveryError):
        fetch_metadata(ISSUER, transport=transport)


@pytest.mark.parametrize(
    "field", ["authorization_endpoint", "token_endpoint", "jwks_uri"]
)
def test_a_missing_endpoint_is_refused(field: str) -> None:
    document = _document()
    del document[field]
    with pytest.raises(DiscoveryError):
        fetch_metadata(ISSUER, transport=RecordingTransport(document=document))


# ── Caching ─────────────────────────────────────────────────────────────────


def test_discovery_is_fetched_once_not_once_per_login() -> None:
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport)

    for _ in range(5):
        cache.metadata(now=100.0)

    assert transport.discovery_fetches == 1


def test_the_key_set_is_fetched_once_not_once_per_login() -> None:
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport)

    for _ in range(5):
        cache.signing_key("k1", now=100.0)

    assert transport.jwks_fetches == 1


def test_a_cached_key_set_expires() -> None:
    """Without a TTL a rotated-OUT key stays trusted forever — the cache would
    become a way to keep using a key the provider has retired."""
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport, jwks_ttl=3600.0)

    cache.signing_key("k1", now=100.0)
    cache.signing_key("k1", now=100.0 + 3601)

    assert transport.jwks_fetches == 2


def test_an_unknown_kid_forces_one_refetch_so_rotation_works() -> None:
    """The rotation path. The provider signs with a new key; we have never seen
    its `kid`; one forced refetch picks it up instead of every login failing
    until the TTL lapses."""
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport, jwks_min_refetch=0.0)
    cache.signing_key("k1", now=100.0)

    transport.keys = [{"kid": "k1", "kty": "RSA"}, {"kid": "k2", "kty": "RSA"}]
    assert cache.signing_key("k2", now=100.0)["kid"] == "k2"


def test_a_stream_of_unknown_kids_does_not_amplify_into_the_provider() -> None:
    """The guard on the rule above. Without a floor between forced refetches, an
    attacker sending tokens with random `kid` values turns this relying party
    into a request amplifier pointed at the identity provider."""
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport, jwks_min_refetch=60.0)
    cache.signing_key("k1", now=100.0)
    before = transport.jwks_fetches

    for i in range(20):
        with pytest.raises(JWKSError):
            cache.signing_key(f"random-{i}", now=100.0)

    assert transport.jwks_fetches == before, "each unknown kid cost a fetch"


def test_a_failed_unknown_kid_refresh_is_rate_limited_by_attempt() -> None:
    """A failed fetch must move the bound too.

    Stamping only successful refreshes lets an unavailable provider be called
    once for every hostile token, which is the amplifier the floor exists to
    prevent.
    """
    transport = RecordingTransport()
    cache = ProviderCache(ISSUER, transport=transport, jwks_min_refetch=60.0)
    cache.signing_key("k1", now=100.0)
    transport.fail_jwks = True

    with pytest.raises(JWKSError):
        cache.signing_key("rotated-1", now=200.0)
    after_failure = transport.jwks_fetches
    with pytest.raises(JWKSError):
        cache.signing_key("rotated-2", now=201.0)

    assert transport.jwks_fetches == after_failure


def test_a_failed_unknown_kid_refresh_keeps_the_working_keys() -> None:
    """A rotation fetch is additive until it succeeds.

    A failed attempt for a new key must not turn a provider outage into the
    invalidation of a known key that remains inside its cache TTL.
    """
    transport = RecordingTransport()
    cache = ProviderCache(
        ISSUER,
        transport=transport,
        jwks_ttl=3600.0,
        jwks_min_refetch=60.0,
    )
    assert cache.signing_key("k1", now=100.0)["kid"] == "k1"
    transport.fail_jwks = True

    with pytest.raises(JWKSError):
        cache.signing_key("rotated", now=200.0)

    assert cache.signing_key("k1", now=201.0)["kid"] == "k1"


# ── The redirect boundary ───────────────────────────────────────────────────


class _RecordingHttpxClient:
    """Stands in for a consumer's pooled `httpx.Client(follow_redirects=True)`."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def _respond(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Response:
            status_code = 200
            content = b"{}"

            def json(_self) -> dict[str, Any]:
                return dict(self.payload)

        return _Response()

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._respond(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._respond(url, **kwargs)


def test_an_injected_client_cannot_re_enable_redirect_following() -> None:
    """A consumer's shared client commonly sets `follow_redirects=True`, which
    is a fine default for an application and wrong for this package: the
    discovery document, key set and token response would become whatever the
    LAST host in a redirect chain returned, not the host the operator
    configured. The per-request argument is what makes the client-level setting
    unable to win."""
    from dotmac_auth_oidc import HttpxTransport

    client = _RecordingHttpxClient(_document())
    transport = HttpxTransport(client)

    transport.get_json(f"{ISSUER}/.well-known/openid-configuration", timeout=5.0)
    transport.post_form(f"{ISSUER}/token", data={}, auth=None, timeout=5.0)

    assert client.calls, "the injected client was never used"
    for call in client.calls:
        assert call.get("follow_redirects") is False, (
            "a request left this package without follow_redirects=False — an "
            "injected client's own setting would then decide the trust boundary"
        )


def test_a_key_set_with_no_addressable_key_is_refused() -> None:
    """A key with no `kid` can only be selected by guessing, and "try every key
    until one verifies" is how an unrelated key ends up validating a token."""
    transport = RecordingTransport(keys=[{"kty": "RSA"}])
    cache = ProviderCache(ISSUER, transport=transport)

    with pytest.raises(JWKSError, match="kid"):
        cache.signing_key("k1", now=100.0)


def test_an_empty_key_set_is_refused() -> None:
    cache = ProviderCache(ISSUER, transport=RecordingTransport(keys=[]))
    with pytest.raises(JWKSError):
        cache.signing_key("k1", now=100.0)
