"""HTTP transports. The agent has exactly two outbound classes (DESIGN-rev3 s3).

* `HttpsTransport`: the CP target API over HTTPS 443, CA and SPKI pinned at
  enrolment. No redirects, no proxy environment, bounded response size.
* `OpenBaoTransport`: OpenBao over WireGuard only (plain HTTP is the ruled
  transport inside WG), used by the delivery identity for `secret_id`s.

Neither is a registry client: no host holds a registry pull credential.
"""

from __future__ import annotations

import hashlib
import http.client
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .refusal import Refusal

MAX_RESPONSE = 4 * 1024 * 1024
MAX_REQUEST = 256 * 1024
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


class Transport(Protocol):
    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response: ...


def spki_sha256(der_certificate: bytes) -> str:
    certificate = x509.load_der_x509_certificate(der_certificate)
    spki = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(spki).hexdigest()


def _read(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
) -> Response:
    try:
        connection.request(method, path, body=body or None, headers=dict(headers))
        reply = connection.getresponse()
        data = reply.read(MAX_RESPONSE + 1)
    except (OSError, http.client.HTTPException):
        raise Refusal("cp_unreachable") from None
    finally:
        connection.close()
    if len(data) > MAX_RESPONSE:
        raise Refusal("response_malformed")
    collected: dict[str, str] = {}
    for name, value in reply.getheaders():
        key = name.lower()
        # A repeated header is joined, which no signature or id can equal: it is
        # refused, never silently first-or-last (Control's `single_header`).
        collected[key] = f"{collected[key]}, {value}" if key in collected else value
    return Response(reply.status, collected, data)


class HttpsTransport:
    def __init__(
        self, origin: str, *, spki_pins: tuple[str, ...], ca_file: Path | None
    ) -> None:
        parsed = urlparse(origin)
        if parsed.scheme != "https" or not parsed.hostname:
            raise Refusal("config_invalid")
        self._host = parsed.hostname
        self._port = parsed.port or 443
        self._pins = frozenset(spki_pins)
        self._context = ssl.create_default_context(
            cafile=str(ca_file) if ca_file is not None else None
        )
        self._context.minimum_version = ssl.TLSVersion.TLSv1_2

    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        if len(body) > MAX_REQUEST:
            raise Refusal("materials_invalid")
        connection = http.client.HTTPSConnection(
            self._host, self._port, timeout=TIMEOUT_SECONDS, context=self._context
        )
        try:
            connection.connect()
            sock = connection.sock
            der = (
                sock.getpeercert(binary_form=True)
                if isinstance(sock, ssl.SSLSocket)
                else None
            )
            if der is None or spki_sha256(der) not in self._pins:
                connection.close()
                raise Refusal("cp_unreachable")
        except (OSError, http.client.HTTPException):
            connection.close()
            raise Refusal("cp_unreachable") from None
        return _read(connection, method, path, headers, body)


class OpenBaoTransport:
    """Bare-origin HTTP(S) to OpenBao. Used only by the delivery identity."""

    def __init__(self, origin: str) -> None:
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise Refusal("config_invalid")
        self._https = parsed.scheme == "https"
        self._host = parsed.hostname
        self._port = parsed.port or (443 if self._https else 80)

    def request(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        connection: http.client.HTTPConnection
        if self._https:
            connection = http.client.HTTPSConnection(
                self._host, self._port, timeout=TIMEOUT_SECONDS
            )
        else:
            connection = http.client.HTTPConnection(
                self._host, self._port, timeout=TIMEOUT_SECONDS
            )
        return _read(connection, method, path, headers, body)
