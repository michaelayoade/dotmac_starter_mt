"""Single-use Actions OIDC supplier for an explicitly approved exact broker.

The launcher injects Actions' request URL/token and its independently approved
broker origin. No environment/config discovery or workflow permission changes.
OpenBao validates the signed claims; JWT shape validation here is not authority.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from lane3_openbao_topology import (
    AUDIENCE,
    MAX_RESPONSE,
    READ_DEADLINE,
    TopologySourceUnavailable,
    _json_object,
)


def _refuse() -> TopologySourceUnavailable:
    return TopologySourceUnavailable("oidc.request")


def fetch_oidc(url: str, token: str) -> Mapping[str, Any]:
    """Verified direct HTTPS; no proxy, redirects, retries or body/error logging."""
    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(
        parsed.hostname, 443, context=ssl.create_default_context(), timeout=5.0
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
            raise _refuse()
        connection.request(
            "GET",
            parsed.path + "?" + parsed.query,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        reply = connection.getresponse()
        if reply.status != 200:
            raise _refuse()
        raw = reply.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise _refuse()
        value = json.loads(raw, object_pairs_hook=_json_object)
        if not isinstance(value, dict):
            raise _refuse()
        return value
    except Exception:
        raise _refuse() from None
    finally:
        timer.cancel()
        connection.close()


class GithubOidcSupplier:
    """Fresh token for the fixed B7 audience; request credential consumed once."""

    def __init__(
        self,
        *,
        request_url: str,
        request_token: str,
        approved_origin: str,
        fetch: Callable[[str, str], Mapping[str, Any]] = fetch_oidc,
    ) -> None:
        try:
            origin = urlsplit(approved_origin)
            request = urlsplit(request_url)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or not origin.hostname.endswith(".actions.githubusercontent.com")
                or origin.netloc != origin.hostname
                or origin.path
                or origin.query
                or origin.fragment
                or origin.username is not None
                or request.scheme != origin.scheme
                or request.netloc != origin.netloc
                or not request.path.startswith("/")
                or not request.path.endswith("/idtoken")
                or request.fragment
                or any(c.isspace() for c in request_url)
            ):
                raise _refuse()
            pairs = parse_qsl(
                request.query, keep_blank_values=True, strict_parsing=True
            )
            if len(pairs) != 1 or pairs[0][0] != "api-version" or not pairs[0][1]:
                raise _refuse()
            if (
                not isinstance(request_token, str)
                or not request_token
                or len(request_token) > MAX_RESPONSE
                or any(c.isspace() for c in request_token)
            ):
                raise _refuse()
            self._url = request_url + "&" + urlencode({"audience": AUDIENCE})
            self._token: str | None = request_token
            self._fetch = fetch
        except Exception:
            raise _refuse() from None

    def __call__(self, audience: str) -> str:
        token = self._token
        self._token = None
        if audience != AUDIENCE or token is None:
            raise _refuse()
        try:
            reply = self._fetch(self._url, token)
            value = reply.get("value")
            if (
                not isinstance(value, str)
                or len(value) > MAX_RESPONSE
                or re.fullmatch(
                    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value
                )
                is None
            ):
                raise _refuse()
            return value
        except Exception:
            raise _refuse() from None
        finally:
            token = None
