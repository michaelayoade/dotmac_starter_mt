"""Held-secret HTTPS/OCS transport extracted from Sub's Nextcloud boundary."""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

_DEFAULT_TIMEOUT_SECONDS: Final = 20.0
_OCS_HEADERS: Final[Mapping[str, str]] = MappingProxyType(
    {"Accept": "application/json", "OCS-APIRequest": "true"}
)
_SUCCESS_META_CODES: Final = frozenset({100, 200, 201, 202, 204})


class FailureKind(str, Enum):
    """Provider-neutral transport consequence; never a provider status enum."""

    RETRYABLE = "retryable"
    AMBIGUOUS = "ambiguous"
    TERMINAL = "terminal"
    NOT_FOUND = "not_found"


class NextcloudTransportError(RuntimeError):
    """A value-free wire failure safe to cross the connector boundary."""

    def __init__(self, code: str, kind: FailureKind) -> None:
        super().__init__(code)
        self.code = code
        self.kind = kind


@dataclass(frozen=True, slots=True, repr=False)
class ManagementRequest:
    """One closed management call; no caller-controlled path is expressible."""

    capability_id: str
    operation: str
    body: Mapping[str, object]
    mutating: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", MappingProxyType(dict(self.body)))


class NextcloudTransport(Protocol):
    """Injected seam used by the plugin; implementations own only wire I/O."""

    def invoke(
        self,
        *,
        management_endpoint: str,
        management_authorization: str,
        client_secret: str | None,
        request: ManagementRequest,
    ) -> Mapping[str, object]: ...


HostResolver = Callable[
    [str, int], tuple[tuple[object, ...], ...] | list[tuple[object, ...]]
]


def _system_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    return list(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))


def _require_public_host(host: str, port: int, resolver: HostResolver) -> None:
    try:
        answers = resolver(host, port)
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for answer in answers:
            socket_address = answer[4]
            if not isinstance(socket_address, tuple) or not socket_address:
                raise ValueError("management_endpoint_dns_unavailable")
            addresses.add(ipaddress.ip_address(str(socket_address[0])))
    except (IndexError, OSError, TypeError, ValueError) as exc:
        raise ValueError("management_endpoint_dns_unavailable") from exc
    if not addresses:
        raise ValueError("management_endpoint_dns_unavailable")
    if any(not address.is_global for address in addresses):
        raise ValueError("management_endpoint_unsafe_address")


def normalize_management_endpoint(
    value: object,
    *,
    resolver: HostResolver = _system_resolver,
) -> str:
    """Return a public HTTPS origin/sub-path or fail before provider I/O."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("management_endpoint_required")
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() != "https":
        raise ValueError("management_endpoint_https_required")
    if parsed.hostname is None:
        raise ValueError("management_endpoint_host_required")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("management_endpoint_credentials_forbidden")
    if parsed.query or parsed.fragment:
        raise ValueError("management_endpoint_query_or_fragment_forbidden")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("management_endpoint_local_host_forbidden")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("management_endpoint_port_invalid") from exc
    _require_public_host(host, port, resolver)
    netloc = host if port == 443 else f"{host}:{port}"
    return urlunsplit(("https", netloc, parsed.path.rstrip("/"), "", ""))


_ROUTES: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {
        (capability_id, operation): (
            "/ocs/v2.php/apps/dotmac_managed/api/v1/" f"{resource}/{operation}"
        )
        for capability_id, resource in (
            ("collaboration.application.lifecycle.v1", "application-lifecycle"),
            (
                "collaboration.file-roundtrip.lifecycle.v1",
                "file-roundtrip-lifecycle",
            ),
            (
                "collaboration.user-group-quota.lifecycle.v1",
                "user-group-quota-lifecycle",
            ),
            (
                "collaboration.user-oidc.configuration.lifecycle.v1",
                "user-oidc-configuration-lifecycle",
            ),
        )
        for operation in ("apply", "cancel", "observe", "plan")
    }
)


def management_route(capability_id: str, operation: str) -> str:
    """Resolve only a declared capability/verb pair to a hard-coded route."""

    try:
        return _ROUTES[(capability_id, operation)]
    except KeyError:
        raise ValueError("management_operation_unsupported") from None


def _json_object(raw: bytes, *, status_code: int) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        kind = FailureKind.RETRYABLE if status_code >= 500 else FailureKind.TERMINAL
        raise NextcloudTransportError("provider_response_invalid", kind) from None
    if not isinstance(value, dict):
        raise NextcloudTransportError("provider_response_invalid", FailureKind.TERMINAL)
    return value


def _failure_for_status(status_code: int) -> NextcloudTransportError:
    if status_code == 404:
        return NextcloudTransportError(
            "provider_resource_not_found", FailureKind.NOT_FOUND
        )
    if status_code == 429:
        return NextcloudTransportError("provider_rate_limited", FailureKind.RETRYABLE)
    if status_code >= 500:
        return NextcloudTransportError("provider_unavailable", FailureKind.RETRYABLE)
    if status_code == 401:
        return NextcloudTransportError(
            "provider_authentication_failed", FailureKind.TERMINAL
        )
    if status_code == 403:
        return NextcloudTransportError("provider_forbidden", FailureKind.TERMINAL)
    return NextcloudTransportError("provider_rejected", FailureKind.TERMINAL)


def _ocs_data(response: httpx.Response) -> Mapping[str, object]:
    payload = _json_object(response.content, status_code=response.status_code)
    ocs = payload.get("ocs")
    if not isinstance(ocs, Mapping):
        raise NextcloudTransportError("provider_response_invalid", FailureKind.TERMINAL)
    meta = ocs.get("meta")
    if not isinstance(meta, Mapping):
        raise NextcloudTransportError("provider_response_invalid", FailureKind.TERMINAL)
    raw_status = meta.get("statuscode", response.status_code)
    try:
        status_code = int(str(raw_status))
    except (TypeError, ValueError):
        raise NextcloudTransportError(
            "provider_response_invalid", FailureKind.TERMINAL
        ) from None
    if status_code not in _SUCCESS_META_CODES and not 200 <= status_code < 300:
        raise _failure_for_status(status_code)
    data = ocs.get("data")
    if not isinstance(data, Mapping):
        raise NextcloudTransportError("provider_response_invalid", FailureKind.TERMINAL)
    return MappingProxyType(dict(data))


class HttpxNextcloudTransport:
    """Default no-redirect client for the closed management route table."""

    def __init__(
        self,
        *,
        resolver: HostResolver = _system_resolver,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds_invalid")
        self._resolver = resolver
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport

    def invoke(
        self,
        *,
        management_endpoint: str,
        management_authorization: str,
        client_secret: str | None,
        request: ManagementRequest,
    ) -> Mapping[str, object]:
        endpoint = normalize_management_endpoint(
            management_endpoint,
            resolver=self._resolver,
        )
        if not management_authorization or any(
            marker in management_authorization for marker in ("\r", "\n")
        ):
            raise NextcloudTransportError(
                "management_authorization_invalid", FailureKind.TERMINAL
            )
        if client_secret is not None and (
            not client_secret or any(marker in client_secret for marker in ("\r", "\n"))
        ):
            raise NextcloudTransportError("client_secret_invalid", FailureKind.TERMINAL)
        headers = dict(_OCS_HEADERS)
        headers["Authorization"] = management_authorization
        if client_secret is not None:
            headers["X-Dotmac-Held-Client-Secret"] = client_secret
        try:
            with httpx.Client(
                follow_redirects=False,
                timeout=self._timeout_seconds,
                transport=self._http_transport,
                trust_env=False,
            ) as client:
                response = client.request(
                    "POST",
                    endpoint
                    + management_route(request.capability_id, request.operation),
                    headers=headers,
                    params={"format": "json"},
                    json=dict(request.body),
                )
        except httpx.ConnectTimeout:
            raise NextcloudTransportError(
                "provider_connect_timeout", FailureKind.RETRYABLE
            ) from None
        except httpx.TimeoutException:
            kind = FailureKind.AMBIGUOUS if request.mutating else FailureKind.RETRYABLE
            code = (
                "provider_outcome_ambiguous" if request.mutating else "provider_timeout"
            )
            raise NextcloudTransportError(code, kind) from None
        except httpx.RequestError:
            raise NextcloudTransportError(
                "provider_unavailable", FailureKind.RETRYABLE
            ) from None
        if 300 <= response.status_code < 400:
            raise NextcloudTransportError(
                "provider_redirect_rejected", FailureKind.TERMINAL
            )
        return _ocs_data(response)


__all__ = [
    "FailureKind",
    "HttpxNextcloudTransport",
    "ManagementRequest",
    "NextcloudTransport",
    "NextcloudTransportError",
    "management_route",
    "normalize_management_endpoint",
]
