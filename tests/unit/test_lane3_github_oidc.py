"""Actions OIDC supplier sensitivity cases; hosted CI only, synthetic tokens."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_github_oidc", ROOT / "scripts/lane3_github_oidc.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
ORIGIN = "https://fixture.actions.githubusercontent.com"
URL = ORIGIN + "/synthetic/jobs/job/idtoken?api-version=2.0"


def supplier(**kwargs: Any) -> Any:
    args = {
        "request_url": URL,
        "approved_origin": ORIGIN,
        "request_token": "synthetic-request-token",
        "fetch": lambda u, t: {"value": "a.b.c"},
    }
    args.update(kwargs)
    return m.GithubOidcSupplier(**args)


def test_exact_broker_fixed_audience_and_single_use() -> None:
    calls = []
    s = supplier(fetch=lambda u, t: calls.append((u, t)) or {"value": "a.b.c"})
    assert s(m.AUDIENCE) == "a.b.c"
    assert parse_qs(urlsplit(calls[0][0]).query)["audience"] == [m.AUDIENCE]
    assert calls[0][1] == "synthetic-request-token"
    assert "synthetic-request-token" not in repr(s)
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://fixture.actions.githubusercontent.com/idtoken?api-version=2.0",
        "https://other.actions.githubusercontent.com/idtoken?api-version=2.0",
        "https://fixture.actions.githubusercontent.com.evil.invalid/idtoken?api-version=2.0",
        "https://user@fixture.actions.githubusercontent.com/idtoken?api-version=2.0",
        URL + "&audience=other",
        URL + "&api-version=3",
        URL + "#fragment",
        ORIGIN + "/other?api-version=2.0",
        ORIGIN + "/idtoken",
    ],
)
def test_unapproved_destination_or_query_refuses_before_fetch(url: str) -> None:
    calls = []
    with pytest.raises(m.TopologySourceUnavailable):
        supplier(request_url=url, fetch=lambda *a: calls.append(a))
    assert calls == []


def test_wrong_audience_never_fetches_and_consumes_credential() -> None:
    calls = []
    s = supplier(fetch=lambda *a: calls.append(a))
    with pytest.raises(m.TopologySourceUnavailable):
        s("other")
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert calls == []


@pytest.mark.parametrize(
    "reply",
    [{}, {"value": ""}, {"value": "not-jwt"}, {"value": 1}, {"value": "a.b.c\n"}],
)
def test_bad_token_response_refuses(reply: Any) -> None:
    with pytest.raises(m.TopologySourceUnavailable):
        supplier(fetch=lambda *a: reply)(m.AUDIENCE)


def test_fetch_failure_redacted_and_not_retried() -> None:
    def fail(*args: Any) -> Any:
        raise RuntimeError("PRIVATE-TOKEN")

    s = supplier(fetch=fail)
    with pytest.raises(m.TopologySourceUnavailable) as e:
        s(m.AUDIENCE)
    assert "PRIVATE-TOKEN" not in str(e.value)
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)


# --- grant-bound pinned transport (synthetic sockets, no network, no DNS) ----

import io  # noqa: E402
import socket  # noqa: E402
import ssl  # noqa: E402

HOST = "fixture.actions.githubusercontent.com"
ADDR4 = "192.0.2.10"
ADDR6 = "2001:db8::10"
NOW = 1_000_000_000_000


class FakeTls:
    def __init__(self, response: bytes) -> None:
        self.sent = b""
        self.response = response
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def makefile(self, mode: str = "rb", *a: Any, **k: Any) -> io.BytesIO:
        return io.BytesIO(self.response)

    def settimeout(self, value: Any) -> None:
        pass

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeRaw:
    def __init__(self, world: "World") -> None:
        self.world = world
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.world.timeouts.append(value)

    def connect(self, addr: Any) -> None:
        self.world.connects.append(addr)
        if self.world.connect_error:
            raise OSError("PRIVATE-CONNECT-DETAIL")

    def getpeername(self) -> Any:
        return self.world.peer

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, world: "World", *, verify: Any, check: bool) -> None:
        self.world = world
        self.verify_mode = verify
        self.check_hostname = check

    def wrap_socket(self, raw: FakeRaw, server_hostname: str) -> FakeTls:
        self.world.wrapped.append(server_hostname)
        tls = FakeTls(self.world.response)
        self.world.tls = tls
        return tls


def http_reply(status: str = "200 OK", body: bytes = b'{"value": "a.b.c"}') -> bytes:
    return (
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
    )


class World:
    def __init__(self, **kw: Any) -> None:
        self.family = kw.get("family", socket.AF_INET)
        self.address = kw.get("address", ADDR4)
        self.peer = kw.get("peer", (self.address, 443))
        self.response = kw.get("response", http_reply())
        self.connect_error = kw.get("connect_error", False)
        self.verify = kw.get("verify", ssl.CERT_REQUIRED)
        self.check = kw.get("check", True)
        self.deadline = kw.get("deadline", NOW + 30_000_000_000)
        self.connects: list[Any] = []
        self.timeouts: list[float] = []
        self.wrapped: list[str] = []
        self.created: list[FakeRaw] = []
        self.tls: FakeTls | None = None

    def raw(self, family: int, kind: int) -> FakeRaw:
        assert family == self.family and kind == socket.SOCK_STREAM
        r = FakeRaw(self)
        self.created.append(r)
        return r

    def target(self) -> Any:
        return m.PinnedBrokerTarget(
            origin=ORIGIN,
            family=self.family,
            address=self.address,
            deadline_ns=self.deadline,
        )

    def fetcher(self) -> Any:
        return m.PinnedOidcFetcher(
            self.target(),
            clock_ns=lambda: NOW,
            context_factory=lambda: FakeContext(self, verify=self.verify, check=self.check),
            socket_factory=self.raw,
        )

    def supplier(self, **kw: Any) -> Any:
        args: dict[str, Any] = {
            "request_url": URL,
            "approved_origin": ORIGIN,
            "request_token": "synthetic-request-token",
            "pinned": self.target(),
        }
        args.update(kw)
        s = m.GithubOidcSupplier(**args)
        s._fetch = self.fetcher()
        return s


@pytest.fixture(autouse=True)
def no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("DNS lookup attempted")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, boom)


def test_pinned_happy_path_numeric_connect_sni_host_and_single_header_token() -> None:
    w = World()
    assert w.supplier()(m.AUDIENCE) == "a.b.c"
    assert w.connects == [(ADDR4, 443)]
    assert w.wrapped == [HOST]
    sent = w.tls.sent  # type: ignore[union-attr]
    assert f"Host: {HOST}\r\n".encode() in sent
    assert sent.count(b"synthetic-request-token") == 1
    assert b"Authorization: Bearer synthetic-request-token\r\n" in sent
    assert sent.startswith(b"GET /synthetic/jobs/job/idtoken?api-version=2.0&audience=")
    assert len(w.created) == 1


def test_pinned_ipv6_uses_four_tuple_and_matches_peer() -> None:
    w = World(family=socket.AF_INET6, address=ADDR6, peer=(ADDR6, 443, 0, 0))
    assert w.supplier()(m.AUDIENCE) == "a.b.c"
    assert w.connects == [(ADDR6, 443, 0, 0)]


def test_pinned_peer_address_mismatch_sends_nothing() -> None:
    w = World(peer=("192.0.2.99", 443))
    with pytest.raises(m.TopologySourceUnavailable):
        w.supplier()(m.AUDIENCE)
    assert w.wrapped == [] and w.tls is None
    assert all(r.closed for r in w.created)


def test_pinned_peer_family_or_port_mismatch_refuses() -> None:
    for peer in (("192.0.2.10", 8443), (ADDR6, 443, 0, 0), "junk", ("not-an-ip", 443)):
        w = World(peer=peer)
        with pytest.raises(m.TopologySourceUnavailable):
            w.supplier()(m.AUDIENCE)
        assert w.wrapped == []


def test_pinned_peer_sensitivity_matching_peer_succeeds() -> None:
    # Same world as the mismatch cases except the peer matches: proves the
    # refusals above come from the peer predicate and nothing else.
    w = World(peer=(ADDR4, 443))
    assert w.supplier()(m.AUDIENCE) == "a.b.c"


def test_pinned_expired_deadline_never_creates_a_socket() -> None:
    w = World(deadline=NOW)
    with pytest.raises(m.TopologySourceUnavailable):
        w.supplier()(m.AUDIENCE)
    assert w.created == []
    w = World(deadline=NOW - 1)
    with pytest.raises(m.TopologySourceUnavailable):
        w.supplier()(m.AUDIENCE)
    assert w.created == []


def test_pinned_connect_timeout_is_bounded_by_remaining_deadline() -> None:
    w = World(deadline=NOW + 1_000_000_000)
    w.supplier()(m.AUDIENCE)
    assert 0 < w.timeouts[0] <= 1.0
    w = World()
    w.supplier()(m.AUDIENCE)
    assert w.timeouts[0] == m.CONNECT_TIMEOUT


@pytest.mark.parametrize(
    ("verify", "check"), [(ssl.CERT_NONE, True), (ssl.CERT_REQUIRED, False)]
)
def test_pinned_weakened_tls_context_refuses_before_socket(verify: Any, check: bool) -> None:
    w = World(verify=verify, check=check)
    with pytest.raises(m.TopologySourceUnavailable):
        w.supplier()(m.AUDIENCE)
    assert w.created == []


def test_default_context_verifies_certificate_and_hostname() -> None:
    ctx = m._default_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_pinned_redirect_and_error_statuses_refuse_without_second_connection() -> None:
    for status in ("302 Found", "307 Temporary Redirect", "403 Forbidden", "500 x"):
        w = World(response=http_reply(status, b"PRIVATE-BODY"))
        with pytest.raises(m.TopologySourceUnavailable) as e:
            w.supplier()(m.AUDIENCE)
        assert "PRIVATE-BODY" not in str(e.value)
        assert len(w.created) == 1


def test_pinned_connect_failure_is_fixed_label_single_attempt() -> None:
    w = World(connect_error=True)
    s = w.supplier()
    with pytest.raises(m.TopologySourceUnavailable) as e:
        s(m.AUDIENCE)
    assert "PRIVATE" not in str(e.value) and "synthetic-request-token" not in str(e.value)
    assert len(w.connects) == 1
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert len(w.connects) == 1
    assert all(r.closed for r in w.created)


def test_pinned_oversized_or_duplicate_key_response_refuses() -> None:
    for body in (b"x" * (m.MAX_RESPONSE + 1), b'{"value":"a.b.c","value":"d.e.f"}'):
        w = World(response=http_reply(body=body))
        with pytest.raises(m.TopologySourceUnavailable):
            w.supplier()(m.AUDIENCE)


def test_pinned_single_use() -> None:
    w = World()
    s = w.supplier()
    assert s(m.AUDIENCE) == "a.b.c"
    with pytest.raises(m.TopologySourceUnavailable):
        s(m.AUDIENCE)
    assert len(w.created) == 1


def test_pinned_fetcher_refuses_url_for_other_origin_before_socket() -> None:
    w = World()
    fetcher = w.fetcher()
    other = "https://other.actions.githubusercontent.com/x/idtoken?api-version=2.0"
    with pytest.raises(m.TopologySourceUnavailable):
        fetcher(other, "t")
    assert w.created == []


@pytest.mark.parametrize(
    "build",
    [
        lambda w: m.PinnedBrokerTarget(ORIGIN + ":443", w.family, w.address, w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN + "/", w.family, w.address, w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN, 99, w.address, w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN, socket.AF_INET, ADDR6, w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN, socket.AF_INET6, ADDR4, w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN, w.family, "not-an-ip", w.deadline),
        lambda w: m.PinnedBrokerTarget(ORIGIN, w.family, w.address, "soon"),
        lambda w: m.PinnedBrokerTarget(ORIGIN, w.family, w.address, True),
    ],
)
def test_pinned_target_validation_fails_closed(build: Any) -> None:
    w = World()
    with pytest.raises(m.TopologySourceUnavailable):
        m.PinnedOidcFetcher(build(w), socket_factory=w.raw)


def test_pinned_supplier_origin_must_equal_grant_and_fetch_not_overridable() -> None:
    w = World()
    other = "https://other.actions.githubusercontent.com"
    with pytest.raises(m.TopologySourceUnavailable):
        m.GithubOidcSupplier(
            request_url=other + "/x/idtoken?api-version=2.0",
            approved_origin=other,
            request_token="t",
            pinned=w.target(),
        )
    with pytest.raises(m.TopologySourceUnavailable):
        m.GithubOidcSupplier(
            request_url=URL,
            approved_origin=ORIGIN,
            request_token="t",
            pinned=w.target(),
            fetch=lambda *a: {},
        )


def test_legacy_supplier_without_pinned_keeps_connect_time_fetch() -> None:
    s = m.GithubOidcSupplier(
        request_url=URL, approved_origin=ORIGIN, request_token="t"
    )
    assert s._fetch is m.fetch_oidc


@pytest.mark.parametrize(
    "url",
    [
        ORIGIN + "/a/%2e%2e/idtoken?api-version=2.0",
        ORIGIN + "/a/../idtoken?api-version=2.0",
        ORIGIN + "/a//idtoken?api-version=2.0",
        ORIGIN + "/a\\b/idtoken?api-version=2.0",
        ORIGIN + "/a/idtoken?api-version=%32.0",
        ORIGIN + "/a/idtoken?api-version=2.0%",
        ORIGIN + "/a/idtoken?api-version=",
        ORIGIN + "/a/idtoken?api-version=2.0&",
        ORIGIN + "/a/idtoken?api-version=2.0&x=1",
        ORIGIN + "/a/idtoken?api-version=2.0\x00",
        ORIGIN + "/a/idtoken?api-version=2.0\t",
        ORIGIN + "/a/idtoken?api-version=é",
        ORIGIN + ":443/a/idtoken?api-version=2.0",
        ORIGIN.upper() + "/a/idtoken?api-version=2.0",
    ],
)
def test_pinned_malformed_request_url_refuses_before_any_socket(url: str) -> None:
    w = World()
    with pytest.raises(m.TopologySourceUnavailable):
        w.supplier(request_url=url)
    assert w.created == []
