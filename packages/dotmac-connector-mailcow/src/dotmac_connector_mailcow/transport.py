"""Bounded HTTPS transport for Mailcow's supported administrative API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

_METHODS: Final = frozenset({"GET", "POST"})
_MAX_RESPONSE_BYTES: Final = 1_048_576


class FailureKind(StrEnum):
    """Provider-neutral transport consequence."""

    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class MailcowTransportError(RuntimeError):
    """A stable, material-free provider refusal."""

    def __init__(self, code: str, kind: FailureKind) -> None:
        self.code = code
        self.kind = kind
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class MailcowRequest:
    """One closed request whose representation omits API material and body."""

    method: str
    base_endpoint: str
    path: str
    api_key: str = field(repr=False)
    document: object | None = field(default=None, repr=False)
    mutating: bool = False

    def __post_init__(self) -> None:
        if self.method not in _METHODS:
            raise MailcowTransportError("method_refused", FailureKind.TERMINAL)
        if self.method == "GET" and self.document is not None:
            raise MailcowTransportError("get_document_refused", FailureKind.TERMINAL)
        if isinstance(self.document, Mapping):
            object.__setattr__(self, "document", MappingProxyType(dict(self.document)))
        elif isinstance(self.document, list | tuple):
            object.__setattr__(self, "document", tuple(self.document))


@dataclass(frozen=True, slots=True, repr=False)
class MailcowResponse:
    """One bounded provider response; raw content never renders in logs."""

    status_code: int
    body: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise MailcowTransportError("status_invalid", FailureKind.TERMINAL)
        if not isinstance(self.body, bytes):
            raise MailcowTransportError("response_body_invalid", FailureKind.TERMINAL)
        if len(self.body) > _MAX_RESPONSE_BYTES:
            raise MailcowTransportError("response_too_large", FailureKind.TERMINAL)


class MailcowTransport(Protocol):
    """Injected I/O boundary used by the stateless connector logic."""

    def request(self, request: MailcowRequest) -> MailcowResponse: ...


def normalize_admin_endpoint(value: object) -> str:
    """Return one exact HTTPS origin/path and refuse URL confusion."""

    if not isinstance(value, str) or value != value.strip():
        raise ValueError("admin_endpoint_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("admin_endpoint_invalid") from None
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
        raise ValueError("admin_endpoint_invalid")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("admin_endpoint_invalid")
    segments = tuple(segment for segment in parsed.path.split("/") if segment)
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("admin_endpoint_invalid")
    host = parsed.hostname.rstrip(".").casefold()
    netloc = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path.rstrip("/"), "", ""))


def _request_body(document: object | None) -> bytes | None:
    if document is None:
        return None
    try:
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError):
        raise MailcowTransportError(
            "request_document_invalid", FailureKind.TERMINAL
        ) from None


class HttpxMailcowTransport:
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
        return "HttpxMailcowTransport()"

    def request(self, request: MailcowRequest) -> MailcowResponse:
        endpoint = normalize_admin_endpoint(request.base_endpoint)
        if not request.path.startswith("/api/v1/") or any(
            marker in request.path for marker in ("?", "#", "\\", "%", "//")
        ):
            raise MailcowTransportError("path_refused", FailureKind.TERMINAL)
        if not request.api_key or any(
            marker in request.api_key for marker in ("\r", "\n")
        ):
            raise MailcowTransportError(
                "admin_material_unavailable", FailureKind.TERMINAL
            )
        body = _request_body(request.document)
        headers = {"accept": "application/json", "x-api-key": request.api_key}
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            with self._client.stream(
                request.method,
                endpoint + request.path,
                content=body,
                headers=headers,
            ) as response:
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_length = int(declared)
                    except ValueError:
                        raise MailcowTransportError(
                            "response_length_invalid", FailureKind.TERMINAL
                        ) from None
                    if declared_length > _MAX_RESPONSE_BYTES:
                        raise MailcowTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_RESPONSE_BYTES:
                        raise MailcowTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                return MailcowResponse(response.status_code, bytes(payload))
        except MailcowTransportError:
            raise
        except httpx.TimeoutException:
            kind = FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE
            code = (
                "provider_outcome_unknown" if request.mutating else "provider_timeout"
            )
            raise MailcowTransportError(code, kind) from None
        except httpx.NetworkError:
            kind = FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE
            code = (
                "provider_outcome_unknown"
                if request.mutating
                else "provider_unavailable"
            )
            raise MailcowTransportError(code, kind) from None
        except httpx.HTTPError:
            raise MailcowTransportError(
                "provider_transport_failed", FailureKind.RETRYABLE
            ) from None


__all__ = [
    "FailureKind",
    "HttpxMailcowTransport",
    "MailcowRequest",
    "MailcowResponse",
    "MailcowTransport",
    "MailcowTransportError",
    "normalize_admin_endpoint",
]
