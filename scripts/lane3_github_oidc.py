"""Single-use Actions OIDC supplier for an explicitly approved exact broker.

The launcher injects Actions' request URL/token and its independently approved
broker origin. No environment/config discovery or workflow permission changes.
OpenBao validates the signed claims; JWT shape validation here is not authority.
"""

from __future__ import annotations

import dataclasses
import http.client
import ipaddress
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


BROKER_PORT = 443
CONNECT_TIMEOUT = 5.0


@dataclasses.dataclass(frozen=True)
class PinnedBrokerTarget:
    """One verified job-bound grant reduced to what the transport may use.

    ``origin`` is the canonical granted origin. ``family``/``address`` is the one
    numeric endpoint chosen from the controller's DNS snapshot. ``deadline_ns``
    is the grant's connection deadline on the host's monotonic clock
    (min(lease expiry, snapshot expiry)); no connection starts after it.
    """

    origin: str
    family: int
    address: str = dataclasses.field(repr=False)
    deadline_ns: int


def _refuse_pinned() -> TopologySourceUnavailable:
    return TopologySourceUnavailable("oidc.pinned")


def _default_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _same_address(family: int, left: str, right: str) -> bool:
    try:
        a = ipaddress.ip_address(left)
        b = ipaddress.ip_address(right)
    except ValueError:
        return False
    want = 4 if family == socket.AF_INET else 6
    return a.version == want and b.version == want and a == b


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to ONE numeric address; TLS name and HTTP Host are the approved host.

    No DNS lookup, proxy, redirect, retry or alternate port. The peer address
    is asserted before the TLS handshake, so a mismatch sends nothing.
    """

    def __init__(
        self,
        host: str,
        *,
        family: int,
        address: str,
        context: ssl.SSLContext,
        timeout: float,
        socket_factory: Callable[[int, int], Any],
        deadline_ns: int,
        clock_ns: Callable[[], int],
    ) -> None:
        super().__init__(host, BROKER_PORT, context=context, timeout=timeout)
        self._pin_family = family
        self._pin_address = address
        self._pin_context = context
        self._pin_timeout = timeout
        self._pin_socket_factory = socket_factory
        self._pin_deadline_ns = deadline_ns
        self._pin_clock_ns = clock_ns

    def connect(self) -> None:
        raw = self._pin_socket_factory(self._pin_family, socket.SOCK_STREAM)
        try:
            self.sock = raw
            raw.settimeout(self._pin_timeout)
            if self._pin_family == socket.AF_INET6:
                raw.connect((self._pin_address, BROKER_PORT, 0, 0))
            else:
                raw.connect((self._pin_address, BROKER_PORT))
            peer = raw.getpeername()
            if (
                not isinstance(peer, tuple)
                or len(peer) < 2
                or peer[1] != BROKER_PORT
                or not _same_address(self._pin_family, str(peer[0]), self._pin_address)
            ):
                raise _refuse_pinned()
            left = (self._pin_deadline_ns - self._pin_clock_ns()) / 1e9
            if left <= 0:
                raise _refuse_pinned()
            # wrap_socket detaches raw. Publish the live TLS socket before
            # handshaking so the deadline interrupt closes the actual fd.
            self.sock = self._pin_context.wrap_socket(
                raw, server_hostname=self.host, do_handshake_on_connect=False
            )
            left = (self._pin_deadline_ns - self._pin_clock_ns()) / 1e9
            if left <= 0:
                raise _refuse_pinned()
            self.sock.settimeout(min(self._pin_timeout, left))
            self.sock.do_handshake()

        except BaseException:
            try:
                raw.close()
            except OSError:
                pass
            raise


class PinnedOidcFetcher:
    """``fetch(url, token)`` over a grant-bound, numerically pinned connection."""

    def __init__(
        self,
        target: PinnedBrokerTarget,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        context_factory: Callable[[], ssl.SSLContext] = _default_context,
        socket_factory: Callable[[int, int], Any] = socket.socket,
        request_started: Callable[[], None] | None = None,
    ) -> None:
        if (
            not isinstance(target, PinnedBrokerTarget)
            or not isinstance(target.family, int)
            or isinstance(target.family, bool)
            or target.family not in (socket.AF_INET, socket.AF_INET6)
            or type(target.address) is not str
            or type(target.deadline_ns) is not int
            or not _same_address(target.family, target.address, target.address)
        ):
            raise _refuse_pinned()
        try:
            self._host = origin_split(target.origin).hostname or ""
        except ValueError:
            raise _refuse_pinned() from None
        if not self._host:
            raise _refuse_pinned()
        self._target = target
        self._clock_ns = clock_ns
        self._context_factory = context_factory
        self._socket_factory = socket_factory
        self._request_started = request_started

    def __call__(self, url: str, token: str) -> Mapping[str, Any]:
        try:
            return self._fetch(url, token)
        except Exception:
            raise _refuse() from None

    def _fetch(self, url: str, token: str) -> Mapping[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme + "://" + parsed.netloc != self._target.origin:
            raise _refuse()
        remaining = (self._target.deadline_ns - self._clock_ns()) / 1e9
        if remaining <= 0:
            raise _refuse()
        timeout = min(CONNECT_TIMEOUT, remaining)
        budget = min(READ_DEADLINE, remaining)
        context = self._context_factory()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise _refuse()
        connection = _PinnedConnection(
            self._host,
            family=self._target.family,
            address=self._target.address,
            context=context,
            timeout=timeout,
            socket_factory=self._socket_factory,
            deadline_ns=self._target.deadline_ns,
            clock_ns=self._clock_ns,
        )

        def interrupt() -> None:
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

        timer = threading.Timer(budget, interrupt)
        timer.daemon = True
        started = time.monotonic()
        timer.start()
        try:
            connection.connect()
            if time.monotonic() - started >= budget:
                raise _refuse()
            if self._clock_ns() >= self._target.deadline_ns:
                raise _refuse()
            if self._request_started is not None:
                self._request_started()
            if self._clock_ns() >= self._target.deadline_ns:
                raise _refuse()
            connection.request(
                "GET",
                parsed.path + "?" + parsed.query,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                },
            )
            reply = connection.getresponse()
            if reply.status != 200:
                raise _refuse()
            raw = reply.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise _refuse()
            if self._clock_ns() >= self._target.deadline_ns:
                raise _refuse()
            value = json.loads(raw, object_pairs_hook=_json_object)
            if not isinstance(value, dict):
                raise _refuse()
            return value
        finally:
            timer.cancel()
            connection.close()


def origin_split(origin: str) -> Any:
    """Re-validate a granted origin with the canonical grammar, then split it."""
    from lane3_broker_origin import canonical_origin

    return urlsplit(canonical_origin(origin))


class GithubOidcSupplier:
    """Fresh token for the fixed B7 audience; request credential consumed once.

    Without ``pinned`` the legacy ``fetch_oidc`` resolves the hostname at
    connect time (kept for callers that have no handoff grant). With ``pinned``
    the supplier connects only to the grant's numeric address and its origin
    must equal ``approved_origin``.
    """

    def __init__(
        self,
        *,
        request_url: str,
        request_token: str,
        approved_origin: str,
        fetch: Callable[[str, str], Mapping[str, Any]] | None = None,
        pinned: PinnedBrokerTarget | None = None,
        context_factory: Callable[[], ssl.SSLContext] = _default_context,
        request_started: Callable[[], None] | None = None,
        require_pinned: bool = False,
    ) -> None:
        try:
            if require_pinned and pinned is None:
                raise _refuse()
            if pinned is not None:
                if fetch is not None or pinned.origin != approved_origin:
                    raise _refuse()
                fetch = PinnedOidcFetcher(
                    pinned,
                    context_factory=context_factory,
                    request_started=request_started,
                )
            elif fetch is None:
                fetch = fetch_oidc
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
                or any(
                    ord(c) <= 0x20 or ord(c) >= 0x7F for c in request_url
                )  # whitespace, controls, non-ASCII
                or "%" in request.path
                or "\\" in request.path
                or "//" in request.path
                or ".." in request.path.split("/")
            ):
                raise _refuse()
            pairs = parse_qsl(
                request.query, keep_blank_values=True, strict_parsing=True
            )
            if (
                len(pairs) != 1
                or pairs[0][0] != "api-version"
                or re.fullmatch(r"[A-Za-z0-9._-]+", pairs[0][1]) is None
                or "%" in request.query
            ):
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
