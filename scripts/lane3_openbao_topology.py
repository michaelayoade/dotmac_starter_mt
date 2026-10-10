"""Explicit B7 JWT-to-KV reader; no ambient credentials or automatic activation.

The approved launcher injects a fresh JWT supplier and authenticated transport.
The runner already accepts this object through its ``topology_src`` seam.
Unconfigured/default execution continues to refuse. Batch tokens cannot be
individually revoked: their measured lifetime is bounded, never called revoked.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import lane3_topology_source as source

TopologySourceUnavailable = source.TopologySourceUnavailable
ROLE = "lane3-exposure-rehearsal"
AUDIENCE = "urn:dotmac:lane3:exposure-rehearsal"
LOGIN_PATH = "/v1/auth/jwt/login"
KV_PATH = "/v1/secret/data/dotmac/starter/lane3/vantage-topology"
MAX_RESPONSE = 65536
MAX_TOKEN_TTL = 300
READ_DEADLINE = 15.0


def unavailable(field: str) -> TopologySourceUnavailable:
    return TopologySourceUnavailable(field)


class JsonTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> Mapping[str, Any]: ...


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise unavailable("response.duplicate_field")
        result[key] = value
    return result


class HttpsOpenBaoTransport:
    """Direct TLS, explicit CA file, no proxy, redirects, retries or HTTP fallback."""

    def __init__(self, *, endpoint: str, ca_file: str) -> None:
        try:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in endpoint)
            ):
                raise unavailable("transport.endpoint")
            self._host = parsed.hostname
            self._port = parsed.port or 443
            self._tls = ssl.create_default_context(cafile=ca_file)
            self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            raise unavailable("transport.configuration") from None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> Mapping[str, Any]:
        if not (
            (method == "POST" and path == LOGIN_PATH and token is None)
            or (
                method == "GET"
                and path.startswith(KV_PATH + "?version=")
                and body is None
            )
        ):
            raise unavailable("transport.operation")
        if method == "GET":
            version = path[len(KV_PATH + "?version=") :]
            if not version.isascii() or not version.isdecimal() or int(version) < 1:
                raise unavailable("transport.operation")
        if method == "POST" and (
            not isinstance(body, Mapping)
            or set(body) != {"role", "jwt"}
            or body.get("role") != ROLE
        ):
            raise unavailable("transport.operation")
        connection = http.client.HTTPSConnection(
            self._host, self._port, context=self._tls, timeout=5.0
        )

        def interrupt() -> None:
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

        timer = threading.Timer(READ_DEADLINE, interrupt)
        timer.daemon = True
        started = time.monotonic()
        timer.start()
        try:
            connection.connect()
            if time.monotonic() - started >= READ_DEADLINE:
                raise unavailable("transport.deadline")
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if token is not None:
                headers["X-Vault-Token"] = token
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise unavailable("transport.status")
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise unavailable("transport.response_size")
            decoded = json.loads(raw, object_pairs_hook=_json_object)
            if not isinstance(decoded, dict):
                raise unavailable("transport.response_shape")
            return decoded
        except Exception:
            raise unavailable("transport.request") from None
        finally:
            timer.cancel()
            connection.close()


class OpenBaoTopologySource:
    """Single-use source. JWT, batch token and topology never enter repr or files."""

    def __init__(
        self,
        *,
        transport: JsonTransport,
        jwt_supplier: Callable[[str], str],
        expected_version: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(expected_version) is not int or expected_version < 1:
            raise unavailable("expected_version")
        self._transport = transport
        self._jwt_supplier = jwt_supplier
        self._version = expected_version
        self._clock = clock
        self._consumed = False

    def read(self) -> source.TopologyReading:
        if self._consumed:
            raise unavailable("source.consumed")
        self._consumed = True
        started = self._clock()

        def deadline() -> None:
            if self._clock() - started >= READ_DEADLINE:
                raise unavailable("deadline")

        jwt = token = None
        try:
            jwt = self._jwt_supplier(AUDIENCE)
            if not isinstance(jwt, str) or not jwt or len(jwt) > MAX_RESPONSE:
                raise unavailable("jwt.supplier")
            deadline()
            login = self._transport.request(
                "POST", LOGIN_PATH, body={"role": ROLE, "jwt": jwt}
            )
            jwt = None
            deadline()
            auth = login.get("auth")
            if not isinstance(auth, Mapping):
                raise unavailable("auth.response")
            token = auth.get("client_token")
            ttl = auth.get("lease_duration")
            if (
                not isinstance(token, str)
                or not token
                or len(token) > MAX_RESPONSE
                or auth.get("token_type") != "batch"
                or auth.get("renewable") is not False
                or type(ttl) is not int
                or not 1 <= ttl <= MAX_TOKEN_TTL
                or auth.get("policies") != [ROLE]
                or auth.get("token_policies") != [ROLE]
                or auth.get("identity_policies", []) != []
            ):
                raise unavailable("auth.response")
            authenticated_at = self._clock()
            reply = self._transport.request(
                "GET", KV_PATH + f"?version={self._version}", token=token
            )
            deadline()
            if self._clock() - authenticated_at >= ttl:
                raise unavailable("auth.expired")
            data = reply.get("data")
            if not isinstance(data, Mapping):
                raise unavailable("kv.response")
            metadata = data.get("metadata")
            if (
                not isinstance(metadata, Mapping)
                or type(metadata.get("version")) is not int
                or metadata.get("version") != self._version
                or metadata.get("destroyed") is not False
                or metadata.get("deletion_time") != ""
            ):
                raise unavailable("kv.metadata")
            record = data.get("data")
            if not isinstance(record, Mapping):
                raise unavailable("kv.record")
            reading = source.TopologyReading(record=record, kv_version=self._version)
            try:
                source.parse_reading(reading)
            except Exception:
                raise unavailable("kv.schema") from None
            return reading
        except TopologySourceUnavailable:
            raise
        except Exception:
            raise unavailable("reader.operation") from None
        finally:
            # Removes owned references; Python does not guarantee memory zeroization.
            jwt = token = None
