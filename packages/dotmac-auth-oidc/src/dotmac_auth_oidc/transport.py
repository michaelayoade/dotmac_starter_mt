"""The one place this package touches a network.

Every outbound request in `dotmac-auth-oidc` goes through `Transport`. That is
deliberate and it is testable: the security tests drive real token validation
against a real key pair with a fake transport, which is exactly what ERP's suite
could not do — its `_validate_id_token` and `_exchange_code` are monkeypatched
out in every test, leaving the security-critical core with no coverage at all.

Redirects are never followed, and `HttpxTransport` enforces that PER REQUEST
rather than only on the client it builds — an injected pooled client with
`follow_redirects=True` must not be able to overturn it. A 302 from a discovery
or JWKS URL means the document we end up parsing came from a host the operator
did not configure, and "follow it and see" is how an SSRF becomes a trusted key
set.

A consumer supplying its OWN `Transport` implementation owns this rule itself:
the protocol below cannot enforce what it does not implement, which is stated
there rather than assumed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dotmac_auth_oidc.errors import OIDCError

DEFAULT_TIMEOUT_SECONDS = 10.0

# A provider's discovery document and key set are small. A response far larger
# than either is either a misconfigured URL or something trying to exhaust the
# client, and parsing it to find out is the wrong order of operations.
MAX_RESPONSE_BYTES = 512 * 1024


@runtime_checkable
class Transport(Protocol):
    """The minimal HTTP surface this package needs.

    Two methods, both synchronous. A consumer that wants its own pooled client,
    proxy configuration, retry policy or observability supplies an object with
    these methods and never inherits this package's httpx pin.
    """

    def get_json(self, url: str, *, timeout: float) -> dict[str, object]:
        """GET `url`, expect JSON.

        **Must not follow redirects.** A custom implementation is trusted to
        honour this — nothing here can check it, and a transport that follows a
        302 lets the network choose which key set decides identity.
        """
        ...

    def post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        auth: tuple[str, str] | None,
        timeout: float,
    ) -> dict[str, object]:
        """POST `url` form-encoded, expect JSON. Must not follow redirects."""
        ...


class HttpxTransport:
    """The default `Transport`, over httpx.

    Constructed lazily and imported inside the methods, so importing this
    package does not import httpx — a consumer that injects its own transport
    genuinely does not pay for the dependency.
    """

    __slots__ = ("_client",)

    def __init__(self, client: object | None = None) -> None:
        self._client = client

    def _request(self, method: str, url: str, **kwargs: object) -> dict[str, object]:
        import httpx

        client = self._client
        # PER-REQUEST, on every path — not merely on the client this class
        # constructs. A consumer injecting its own pooled
        # `httpx.Client(follow_redirects=True)` (a perfectly ordinary default
        # for an application's shared client) would otherwise silently overturn
        # this package's trust boundary: the discovery document, the key set and
        # the token response would be whatever the LAST host in a redirect chain
        # returned, which is not the host the operator configured. Passing it
        # explicitly means the caller's client-level setting cannot win.
        kwargs["follow_redirects"] = False
        try:
            if client is None:
                with httpx.Client(follow_redirects=False) as owned:
                    response = getattr(owned, method)(url, **kwargs)
            else:
                response = getattr(client, method)(url, **kwargs)
        except Exception as exc:
            raise OIDCError(f"request to {url!r} failed: {exc}") from exc

        status = getattr(response, "status_code", None)
        if status != 200:
            raise OIDCError(f"request to {url!r} returned HTTP {status}")

        content = getattr(response, "content", b"")
        if len(content) > MAX_RESPONSE_BYTES:
            raise OIDCError(
                f"response from {url!r} is {len(content)} bytes, over the "
                f"{MAX_RESPONSE_BYTES} limit"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise OIDCError(f"response from {url!r} is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise OIDCError(f"response from {url!r} is not a JSON object")
        return payload

    def get_json(self, url: str, *, timeout: float) -> dict[str, object]:
        return self._request("get", url, timeout=timeout)

    def post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        auth: tuple[str, str] | None,
        timeout: float,
    ) -> dict[str, object]:
        return self._request("post", url, data=data, auth=auth, timeout=timeout)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESPONSE_BYTES",
    "HttpxTransport",
    "Transport",
]
