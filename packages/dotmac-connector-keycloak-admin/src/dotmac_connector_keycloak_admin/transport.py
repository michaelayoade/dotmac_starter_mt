"""Bounded HTTPS transport for the realm-scoped Keycloak Admin REST API."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import unquote, urlencode, urlsplit

import httpx

_METHODS: Final = frozenset({"DELETE", "GET", "POST", "PUT"})
_MAX_RESPONSE_BYTES: Final = 1_048_576
_REALM_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")
_ADMIN_CLIENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$"
)


class KeycloakTransportError(RuntimeError):
    """A stable, material-free transport refusal."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class KeycloakAdminRequest:
    """One request whose representation can never render held material."""

    method: str
    base_endpoint: str
    path: str
    access_token: str = field(repr=False)
    query: Mapping[str, str] = field(default_factory=dict)
    document: Mapping[str, object] | tuple[str, ...] | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        if self.method not in _METHODS:
            raise KeycloakTransportError("method_refused")
        object.__setattr__(self, "query", MappingProxyType(dict(self.query)))
        if isinstance(self.document, Mapping):
            object.__setattr__(self, "document", MappingProxyType(dict(self.document)))
        elif self.document is not None and not (
            isinstance(self.document, tuple)
            and self.document
            and all(isinstance(item, str) and item for item in self.document)
        ):
            raise KeycloakTransportError("request_document_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class KeycloakAdminResponse:
    """A bounded provider response; its body is never rendered."""

    status_code: int
    body: bytes = field(default=b"", repr=False)
    location: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise KeycloakTransportError("status_invalid")
        if not isinstance(self.body, bytes):
            raise KeycloakTransportError("response_body_invalid")
        if len(self.body) > _MAX_RESPONSE_BYTES:
            raise KeycloakTransportError("response_too_large")


class KeycloakAdminTransport(Protocol):
    """Injected I/O boundary used by all connector logic."""

    def admin_access_token(
        self, *, base_endpoint: str, realm_ref: str, held_material: str
    ) -> str: ...

    def request(self, request: KeycloakAdminRequest) -> KeycloakAdminResponse: ...


def _canonical_base_endpoint(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise KeycloakTransportError("endpoint_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise KeycloakTransportError("endpoint_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
        or "%" in parsed.path
        or "//" in parsed.path
    ):
        raise KeycloakTransportError("endpoint_invalid")
    if port is not None and not 1 <= port <= 65535:
        raise KeycloakTransportError("endpoint_invalid")
    path_segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if any(segment in {".", ".."} for segment in path_segments):
        raise KeycloakTransportError("endpoint_invalid")
    return value.rstrip("/")


def _require_realm_scoped_path(path: str) -> None:
    if (
        not path.startswith("/admin/realms/")
        or "?" in path
        or "#" in path
        or "\\" in path
        or "%" in path
        or "//" in path
    ):
        raise KeycloakTransportError("path_refused")
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) < 3 or segments[:2] != ("admin", "realms"):
        raise KeycloakTransportError("path_refused")
    realm = unquote(segments[2])
    if realm.casefold() == "master" or any(
        unquote(segment) in {".", ".."} for segment in segments
    ):
        raise KeycloakTransportError("path_refused")


def _request_body(
    document: Mapping[str, object] | tuple[str, ...] | None,
) -> bytes | None:
    if document is None:
        return None
    try:
        return json.dumps(
            dict(document) if isinstance(document, Mapping) else document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise KeycloakTransportError("request_document_invalid") from None


@dataclass(frozen=True, slots=True, repr=False)
class _AdminCredential:
    client_id: str
    client_secret: str = field(repr=False)


def _admin_credential(held_material: str) -> _AdminCredential:
    try:
        credential = json.loads(held_material)
    except (TypeError, json.JSONDecodeError):
        raise KeycloakTransportError("admin_material_invalid") from None
    if not isinstance(credential, dict) or set(credential) != {
        "client_id",
        "client_secret",
    }:
        raise KeycloakTransportError("admin_material_invalid")
    client_id = credential.get("client_id")
    client_secret = credential.get("client_secret")
    if (
        not isinstance(client_id, str)
        or _ADMIN_CLIENT_RE.fullmatch(client_id) is None
        or not isinstance(client_secret, str)
        or not client_secret
    ):
        raise KeycloakTransportError("admin_material_invalid")
    return _AdminCredential(client_id=client_id, client_secret=client_secret)


class HttpxKeycloakTransport:
    """No-redirect, no-environment-proxy, bounded real HTTPS transport."""

    __slots__ = ("_client",)

    def __init__(self) -> None:
        self._client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def __repr__(self) -> str:
        return "HttpxKeycloakTransport()"

    def close(self) -> None:
        """Release owned connection-pool resources."""

        self._client.close()

    def _perform(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        query: Mapping[str, str] | None = None,
    ) -> KeycloakAdminResponse:
        try:
            with self._client.stream(
                method,
                url,
                params={} if query is None else dict(query),
                content=body,
                headers=dict(headers),
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise KeycloakTransportError(
                            "response_length_invalid"
                        ) from None
                    if declared_length > _MAX_RESPONSE_BYTES:
                        raise KeycloakTransportError("response_too_large")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise KeycloakTransportError("response_too_large")
                return KeycloakAdminResponse(
                    status_code=response.status_code,
                    body=bytes(payload),
                    location=response.headers.get("location"),
                )
        except KeycloakTransportError:
            raise
        except httpx.TimeoutException:
            raise KeycloakTransportError("timeout") from None
        except httpx.NetworkError:
            raise KeycloakTransportError("network") from None
        except httpx.HTTPError:
            raise KeycloakTransportError("http_transport") from None

    def admin_access_token(
        self, *, base_endpoint: str, realm_ref: str, held_material: str
    ) -> str:
        endpoint = _canonical_base_endpoint(base_endpoint)
        if _REALM_RE.fullmatch(realm_ref) is None or realm_ref.casefold() == "master":
            raise KeycloakTransportError("path_refused")
        credential = _admin_credential(held_material)
        body = urlencode(
            {
                "client_id": credential.client_id,
                "client_secret": credential.client_secret,
                "grant_type": "client_credentials",
            }
        ).encode("ascii")
        response = self._perform(
            method="POST",
            url=(f"{endpoint}/realms/{realm_ref}/protocol/openid-connect/token"),
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
            body=body,
        )
        if response.status_code in {400, 401}:
            raise KeycloakTransportError("admin_authentication_refused")
        if response.status_code == 403:
            raise KeycloakTransportError("admin_authorization_refused")
        if response.status_code == 429:
            raise KeycloakTransportError("provider_rate_limited")
        if 300 <= response.status_code < 400:
            raise KeycloakTransportError("provider_redirect_refused")
        if response.status_code >= 500:
            raise KeycloakTransportError("provider_unavailable")
        if response.status_code != 200:
            raise KeycloakTransportError("provider_request_refused")
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KeycloakTransportError("provider_response_invalid") from None
        if not isinstance(document, dict):
            raise KeycloakTransportError("provider_response_invalid")
        access_token = document.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise KeycloakTransportError("provider_response_invalid")
        return access_token

    def request(self, request: KeycloakAdminRequest) -> KeycloakAdminResponse:
        endpoint = _canonical_base_endpoint(request.base_endpoint)
        _require_realm_scoped_path(request.path)
        if not request.access_token:
            raise KeycloakTransportError("admin_material_unavailable")
        body = _request_body(request.document)
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {request.access_token}",
        }
        if body is not None:
            headers["content-type"] = "application/json"
        return self._perform(
            method=request.method,
            url=endpoint + request.path,
            headers=headers,
            body=body,
            query=request.query,
        )


__all__ = [
    "HttpxKeycloakTransport",
    "KeycloakAdminRequest",
    "KeycloakAdminResponse",
    "KeycloakAdminTransport",
    "KeycloakTransportError",
]
