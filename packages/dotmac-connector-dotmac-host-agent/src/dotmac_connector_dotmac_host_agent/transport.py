"""Bounded mutually authenticated HTTPS transport for a Dotmac host agent."""

from __future__ import annotations

import ipaddress
import json
import re
import ssl
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

_ALLOWED_METHODS: Final = frozenset({"GET", "POST"})
_MAX_BODY_BYTES: Final = 1_048_576
_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"^/v1/(?:capabilities/(?:host\.[a-z-]+\.lifecycle\.v[1-9][0-9]*)|"
    r"provision/(?:host\.[a-z-]+\.lifecycle\.v[1-9][0-9]*)/"
    r"(?:apply|cancel|observe|plan))$"
)
_HOST_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])$"
)


class FailureKind(StrEnum):
    """Provider-neutral consequence of one transport refusal."""

    AMBIGUOUS = "ambiguous"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class HostAgentTransportError(RuntimeError):
    """A stable error code that never contains TLS or authorization material."""

    def __init__(self, code: str, kind: FailureKind) -> None:
        self.code = code
        self.kind = kind
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class HostAgentRequest:
    """One closed agent request; identity material and body never render."""

    method: str
    base_endpoint: str
    path: str
    identity_ref: str
    held_material: str = field(repr=False)
    request_id: str
    document: Mapping[str, object] | None = field(default=None, repr=False)
    mutating: bool = False

    def __post_init__(self) -> None:
        if self.method not in _ALLOWED_METHODS:
            raise HostAgentTransportError("method_refused", FailureKind.TERMINAL)
        if self.method == "GET" and self.document is not None:
            raise HostAgentTransportError("get_document_refused", FailureKind.TERMINAL)
        if self.document is not None:
            object.__setattr__(self, "document", MappingProxyType(dict(self.document)))


@dataclass(frozen=True, slots=True, repr=False)
class HostAgentResponse:
    """One bounded response whose body is never rendered."""

    status_code: int
    body: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise HostAgentTransportError("status_invalid", FailureKind.TERMINAL)
        if not isinstance(self.body, bytes):
            raise HostAgentTransportError("response_body_invalid", FailureKind.TERMINAL)
        if len(self.body) > _MAX_BODY_BYTES:
            raise HostAgentTransportError("response_too_large", FailureKind.TERMINAL)


class HostAgentTransport(Protocol):
    """Injected I/O boundary for all connector logic."""

    def request(self, request: HostAgentRequest) -> HostAgentResponse: ...


@dataclass(frozen=True, slots=True, repr=False)
class _HeldIdentity:
    identity_ref: str
    expected_origin: str
    ca_certificate_file: str = field(repr=False)
    client_certificate_file: str = field(repr=False)
    client_private_key_file: str = field(repr=False)
    authorization_token: str = field(repr=False)


def normalize_agent_endpoint(value: object) -> str:
    """Return one strict HTTPS origin suitable for a bound mTLS identity."""

    if not isinstance(value, str) or value != value.strip():
        raise ValueError("agent_endpoint_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("agent_endpoint_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("agent_endpoint_invalid")
    host = parsed.hostname.rstrip(".").casefold()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("agent_endpoint_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if _HOST_RE.fullmatch(host) is None:
            raise ValueError("agent_endpoint_invalid") from None
    else:
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("agent_endpoint_invalid")
        host = f"[{host}]" if address.version == 6 else host
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("agent_endpoint_invalid")
    netloc = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", netloc, "", "", ""))


def _safe_file(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\r" in value
        or "\n" in value
        or not PurePath(value).is_absolute()
        or ".." in PurePath(value).parts
    ):
        raise HostAgentTransportError(
            "agent_identity_material_invalid", FailureKind.TERMINAL
        )
    return value


def _held_identity(
    held_material: str,
    *,
    expected_identity_ref: str,
    expected_origin: str,
) -> _HeldIdentity:
    try:
        value = json.loads(held_material)
    except (TypeError, json.JSONDecodeError):
        raise HostAgentTransportError(
            "agent_identity_material_invalid", FailureKind.TERMINAL
        ) from None
    expected_fields = {
        "authorization_token",
        "ca_certificate_file",
        "client_certificate_file",
        "client_private_key_file",
        "expected_origin",
        "identity_ref",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise HostAgentTransportError(
            "agent_identity_material_invalid", FailureKind.TERMINAL
        )
    identity_ref = value.get("identity_ref")
    origin = value.get("expected_origin")
    token = value.get("authorization_token")
    try:
        normalized_origin = normalize_agent_endpoint(origin)
    except ValueError:
        raise HostAgentTransportError(
            "agent_identity_binding_mismatch", FailureKind.TERMINAL
        ) from None
    if (
        not isinstance(identity_ref, str)
        or identity_ref != expected_identity_ref
        or normalized_origin != expected_origin
        or not isinstance(token, str)
        or not token
        or "\r" in token
        or "\n" in token
    ):
        raise HostAgentTransportError(
            "agent_identity_binding_mismatch", FailureKind.TERMINAL
        )
    return _HeldIdentity(
        identity_ref=identity_ref,
        expected_origin=normalized_origin,
        ca_certificate_file=_safe_file(value.get("ca_certificate_file")),
        client_certificate_file=_safe_file(value.get("client_certificate_file")),
        client_private_key_file=_safe_file(value.get("client_private_key_file")),
        authorization_token=token,
    )


def _request_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        raise HostAgentTransportError(
            "request_id_invalid", FailureKind.TERMINAL
        ) from None
    if str(parsed) != value.casefold():
        raise HostAgentTransportError("request_id_invalid", FailureKind.TERMINAL)
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
        raise HostAgentTransportError(
            "request_document_invalid", FailureKind.TERMINAL
        ) from None
    if len(payload) > _MAX_BODY_BYTES:
        raise HostAgentTransportError("request_too_large", FailureKind.TERMINAL)
    return payload


class HttpxHostAgentTransport:
    """Real no-redirect/no-proxy mTLS transport, configured per invocation."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "HttpxHostAgentTransport()"

    def request(self, request: HostAgentRequest) -> HostAgentResponse:
        endpoint = normalize_agent_endpoint(request.base_endpoint)
        if _PATH_RE.fullmatch(request.path) is None:
            raise HostAgentTransportError("path_refused", FailureKind.TERMINAL)
        request_id = _request_id(request.request_id)
        identity = _held_identity(
            request.held_material,
            expected_identity_ref=request.identity_ref,
            expected_origin=endpoint,
        )
        body = _request_body(request.document)
        try:
            tls_context = ssl.create_default_context(
                cafile=identity.ca_certificate_file
            )
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_context.load_cert_chain(
                certfile=identity.client_certificate_file,
                keyfile=identity.client_private_key_file,
            )
        except (OSError, ValueError, ssl.SSLError):
            raise HostAgentTransportError(
                "agent_identity_material_unavailable", FailureKind.TERMINAL
            ) from None
        headers = {
            "accept": "application/json",
            "authorization": f"Bearer {identity.authorization_token}",
            "x-request-id": request_id,
        }
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            with (
                httpx.Client(
                    verify=tls_context,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=30.0,
                        write=10.0,
                        pool=5.0,
                    ),
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                    ),
                ) as client,
                client.stream(
                    request.method,
                    endpoint + request.path,
                    content=body,
                    headers=headers,
                ) as response,
            ):
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_length = int(declared)
                    except ValueError:
                        raise HostAgentTransportError(
                            "response_length_invalid", FailureKind.TERMINAL
                        ) from None
                    if declared_length > _MAX_BODY_BYTES:
                        raise HostAgentTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_BODY_BYTES:
                        raise HostAgentTransportError(
                            "response_too_large", FailureKind.TERMINAL
                        )
                return HostAgentResponse(response.status_code, bytes(payload))
        except HostAgentTransportError:
            raise
        except httpx.TimeoutException:
            raise HostAgentTransportError(
                "agent_outcome_unknown" if request.mutating else "agent_timeout",
                FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE,
            ) from None
        except httpx.NetworkError:
            raise HostAgentTransportError(
                "agent_outcome_unknown" if request.mutating else "agent_unavailable",
                FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE,
            ) from None
        except httpx.HTTPError:
            raise HostAgentTransportError(
                "agent_outcome_unknown"
                if request.mutating
                else "agent_transport_failed",
                FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE,
            ) from None


__all__ = [
    "FailureKind",
    "HostAgentRequest",
    "HostAgentResponse",
    "HostAgentTransport",
    "HostAgentTransportError",
    "HttpxHostAgentTransport",
    "normalize_agent_endpoint",
]
