"""Bounded HTTPS transport for the official Contabo API."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

_API_HOST: Final = "api.contabo.com"
_AUTH_ENDPOINT: Final = (
    "https://auth.contabo.com/auth/realms/contabo/" "protocol/openid-connect/token"
)
_ALLOWED_METHODS: Final = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_MAX_RESPONSE_BYTES: Final = 1_048_576


class FailureKind(StrEnum):
    """Provider-neutral consequence of a transport refusal."""

    AMBIGUOUS = "ambiguous"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class ContaboTransportError(RuntimeError):
    """Stable, material-free refusal from the I/O boundary."""

    def __init__(self, code: str, kind: FailureKind) -> None:
        self.code = code
        self.kind = kind
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ContaboRequest:
    """One request; held credential material and body never render."""

    method: str
    base_endpoint: str
    path: str
    held_material: str = field(repr=False)
    request_id: str
    query: Mapping[str, str] = field(default_factory=dict)
    document: Mapping[str, object] | None = field(default=None, repr=False)
    mutating: bool = False

    def __post_init__(self) -> None:
        if self.method not in _ALLOWED_METHODS:
            raise ContaboTransportError("method_refused", FailureKind.TERMINAL)
        object.__setattr__(self, "query", MappingProxyType(dict(self.query)))
        if self.document is not None:
            object.__setattr__(self, "document", MappingProxyType(dict(self.document)))


@dataclass(frozen=True, slots=True, repr=False)
class ContaboResponse:
    """One bounded provider response whose body never renders."""

    status_code: int
    body: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ContaboTransportError("status_invalid", FailureKind.TERMINAL)
        if not isinstance(self.body, bytes):
            raise ContaboTransportError("response_body_invalid", FailureKind.TERMINAL)
        if len(self.body) > _MAX_RESPONSE_BYTES:
            raise ContaboTransportError("response_too_large", FailureKind.TERMINAL)


class ContaboTransport(Protocol):
    """Injected provider I/O used by the stateless connector logic."""

    def request(self, request: ContaboRequest) -> ContaboResponse: ...


@dataclass(frozen=True, slots=True, repr=False)
class _Credential:
    client_id: str
    client_secret: str = field(repr=False)
    username: str
    password: str = field(repr=False)


def _credential(held_material: str) -> _Credential:
    try:
        value = json.loads(held_material)
    except (TypeError, json.JSONDecodeError):
        raise ContaboTransportError(
            "api_material_invalid", FailureKind.TERMINAL
        ) from None
    if not isinstance(value, dict) or set(value) != {
        "client_id",
        "client_secret",
        "password",
        "username",
    }:
        raise ContaboTransportError("api_material_invalid", FailureKind.TERMINAL)
    fields = {name: value.get(name) for name in value}
    if not all(
        isinstance(item, str) and item and "\r" not in item and "\n" not in item
        for item in fields.values()
    ):
        raise ContaboTransportError("api_material_invalid", FailureKind.TERMINAL)
    return _Credential(
        client_id=str(fields["client_id"]),
        client_secret=str(fields["client_secret"]),
        username=str(fields["username"]),
        password=str(fields["password"]),
    )


def normalize_api_endpoint(value: object) -> str:
    """Accept the official API origin and no alternate SSRF target."""

    if not isinstance(value, str) or value != value.strip():
        raise ValueError("api_endpoint_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("api_endpoint_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.rstrip(".").casefold() != _API_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("api_endpoint_invalid")
    return urlunsplit(("https", _API_HOST, "", "", ""))


def _require_safe_path(path: str) -> None:
    if (
        not path.startswith("/v1/firewalls")
        or any(marker in path for marker in ("?", "#", "\\", "%", "//"))
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise ContaboTransportError("path_refused", FailureKind.TERMINAL)


def _require_request_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        raise ContaboTransportError(
            "request_id_invalid", FailureKind.TERMINAL
        ) from None
    if str(parsed) != value.casefold():
        raise ContaboTransportError("request_id_invalid", FailureKind.TERMINAL)
    return str(parsed)


def _request_body(document: Mapping[str, object] | None) -> bytes | None:
    if document is None:
        return None
    try:
        payload = json.dumps(
            dict(document),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ContaboTransportError(
            "request_document_invalid", FailureKind.TERMINAL
        ) from None
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ContaboTransportError("request_too_large", FailureKind.TERMINAL)
    return payload


class HttpxContaboTransport:
    """Real transport with no redirects/proxies and bounded time and bytes."""

    __slots__ = ("_client",)

    def __init__(self) -> None:
        self._client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def __repr__(self) -> str:
        return "HttpxContaboTransport()"

    def close(self) -> None:
        """Release this transport's owned connection pool."""

        self._client.close()

    def _perform(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        query: Mapping[str, str] | None = None,
        mutating: bool,
    ) -> ContaboResponse:
        try:
            with self._client.stream(
                method,
                url,
                params={} if query is None else dict(query),
                content=body,
                headers=dict(headers),
            ) as response:
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_length = int(declared)
                    except ValueError:
                        raise ContaboTransportError(
                            "response_length_invalid", FailureKind.TERMINAL
                        ) from None
                    if declared_length > _MAX_RESPONSE_BYTES:
                        raise ContaboTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise ContaboTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                return ContaboResponse(response.status_code, bytes(payload))
        except ContaboTransportError:
            raise
        except httpx.TimeoutException:
            raise ContaboTransportError(
                "provider_outcome_unknown" if mutating else "provider_timeout",
                FailureKind.AMBIGUOUS if mutating else FailureKind.RETRYABLE,
            ) from None
        except httpx.NetworkError:
            raise ContaboTransportError(
                "provider_outcome_unknown" if mutating else "provider_unavailable",
                FailureKind.AMBIGUOUS if mutating else FailureKind.RETRYABLE,
            ) from None
        except httpx.HTTPError:
            raise ContaboTransportError(
                "provider_outcome_unknown" if mutating else "provider_transport_failed",
                FailureKind.AMBIGUOUS if mutating else FailureKind.RETRYABLE,
            ) from None

    def _access_token(self, held_material: str) -> str:
        credential = _credential(held_material)
        body = urlencode(
            {
                "client_id": credential.client_id,
                "client_secret": credential.client_secret,
                "grant_type": "password",
                "password": credential.password,
                "username": credential.username,
            }
        ).encode("utf-8")
        response = self._perform(
            method="POST",
            url=_AUTH_ENDPOINT,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
            body=body,
            mutating=False,
        )
        if 300 <= response.status_code < 400:
            raise ContaboTransportError(
                "provider_redirect_refused", FailureKind.TERMINAL
            )
        if response.status_code in {400, 401}:
            raise ContaboTransportError(
                "provider_authentication_refused", FailureKind.TERMINAL
            )
        if response.status_code == 403:
            raise ContaboTransportError(
                "provider_authorization_refused", FailureKind.TERMINAL
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ContaboTransportError("provider_unavailable", FailureKind.RETRYABLE)
        if response.status_code != 200:
            raise ContaboTransportError(
                "provider_request_refused", FailureKind.TERMINAL
            )
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContaboTransportError(
                "provider_response_invalid", FailureKind.TERMINAL
            ) from None
        token = document.get("access_token") if isinstance(document, dict) else None
        if not isinstance(token, str) or not token:
            raise ContaboTransportError(
                "provider_response_invalid", FailureKind.TERMINAL
            )
        return token

    def request(self, request: ContaboRequest) -> ContaboResponse:
        endpoint = normalize_api_endpoint(request.base_endpoint)
        _require_safe_path(request.path)
        request_id = _require_request_id(request.request_id)
        token = self._access_token(request.held_material)
        body = _request_body(request.document)
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {token}",
            "x-request-id": request_id,
        }
        if body is not None:
            headers["content-type"] = "application/json"
        return self._perform(
            method=request.method,
            url=endpoint + request.path,
            headers=headers,
            body=body,
            query=request.query,
            mutating=request.mutating,
        )


__all__ = [
    "ContaboRequest",
    "ContaboResponse",
    "ContaboTransport",
    "ContaboTransportError",
    "FailureKind",
    "HttpxContaboTransport",
    "normalize_api_endpoint",
]
