"""Hosted-only capture canaries using the canonical resolver and synthetic DNS wire.

Only the TCP exchange boundary is injected. No sockets or external DNS are used;
query construction, response parsing, alias policy and snapshots remain real.
The small wire encoder follows the existing resolver/network test fixtures.
"""

from __future__ import annotations

import ipaddress
import struct
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import lane3_campaign_bootstrap as cb
import lane3_handoff_resolver as hr

RESOLVERS = ("8.8.8.8", "1.1.1.1")
HOST = "bootstrap.example"
EDGE = "edge.example.net"
V4 = "8.8.8.8"
V6 = "2606:4700::1"
NS = 1_000_000_000
Record = tuple[str, int, int, str]
Zone = Callable[[str, str, int], list[Record]]


def config(*rows: tuple[str, tuple[str, ...]]) -> cb.BootstrapConfig:
    return cb.BootstrapConfig(
        RESOLVERS,
        tuple(
            ("https://" + host, hr.AliasPolicy(frozenset(aliases), frozenset()))
            for host, aliases in (rows or ((HOST, (EDGE,)),))
        ),
        "a" * 64,
    )


def name_wire(name: str) -> bytes:
    return (
        b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split(".")) + b"\0"
    )


def question(query: bytes) -> tuple[str, int]:
    # Canonical encode_query emits an uncompressed question, never pointers.
    labels = []
    offset = 12
    while query[offset]:
        size = query[offset]
        labels.append(query[offset + 1 : offset + 1 + size].decode("ascii"))
        offset += 1 + size
    return ".".join(labels), struct.unpack(">H", query[-4:-2])[0]


def response(query: bytes, answers: list[Record]) -> bytes:
    out = query[:2] + struct.pack(">HHHHH", 0x8180, 1, len(answers), 0, 0)
    out += query[12:]
    for owner, rtype, ttl, value in answers:
        data = (
            ipaddress.ip_address(value).packed
            if rtype in (hr.TYPE_A, hr.TYPE_AAAA)
            else name_wire(value)
        )
        out += name_wire(owner) + struct.pack(">HHIH", rtype, 1, ttl, len(data)) + data
    return out


def records(
    host: str,
    qtype: int,
    *,
    alias: str = EDGE,
    ttl: int = 45,
    address: str | None = None,
) -> list[Record]:
    return [
        (host, hr.TYPE_CNAME, ttl, alias),
        (alias, qtype, ttl, address or (V4 if qtype == hr.TYPE_A else V6)),
    ]


class Clock:
    def __init__(self) -> None:
        self.now = NS

    def __call__(self) -> int:
        return self.now


class WireTCP:
    def __init__(self, zone: Zone, clock: Clock, advance_ns: int = 0) -> None:
        self.zone = zone
        self.clock = clock
        self.advance_ns = advance_ns
        self.calls: list[tuple[str, str, int, float, int | None]] = []

    def __call__(
        self, resolver: str, *, deadline_s: float, deadline_ns: int | None = None
    ) -> Callable[[bytes], bytes]:
        def exchange(query: bytes) -> bytes:
            host, qtype = question(query)
            self.calls.append((resolver, host, qtype, deadline_s, deadline_ns))
            raw = response(query, self.zone(resolver, host, qtype))
            self.clock.now += self.advance_ns
            return raw

        return exchange


@pytest.fixture(autouse=True)
def serial_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep clock progression deterministic; the real executor/capture stays in use.
    monkeypatch.setattr(cb, "MAX_WORKERS", 1)


def install(
    monkeypatch: pytest.MonkeyPatch, zone: Zone, *, advance_ns: int = 0
) -> tuple[Clock, WireTCP]:
    clock = Clock()
    tcp = WireTCP(zone, clock, advance_ns)
    monkeypatch.setattr(hr, "tcp_exchange", tcp)
    return clock, tcp


def test_real_resolver_captures_both_families_twice_with_exact_origin_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second, second_edge = "other.example", "other-edge.example.net"
    aliases = {HOST: EDGE, second: second_edge}
    clock, tcp = install(
        monkeypatch,
        lambda resolver, host, qtype: records(host, qtype, alias=aliases[host]),
    )
    frozen = cb.capture(
        config((HOST, (EDGE,)), (second, (second_edge,))),
        deadline_ns=21 * NS,
        clock_ns=clock,
    )
    assert len(frozen.snapshots) == 4
    assert Counter((r, h, q) for r, h, q, _, _ in tcp.calls) == Counter(
        (resolver, host, qtype)
        for resolver in RESOLVERS
        for host in aliases
        for qtype in (hr.TYPE_A, hr.TYPE_AAAA)
    )
    for snapshot in frozen.snapshots:
        host = snapshot.origin.removeprefix("https://")
        assert snapshot.chain == (host, aliases[host])
        assert snapshot.addresses == ((4, V4), (6, V6))
        assert snapshot.ttl_s == 45
        assert snapshot.expires_at_monotonic_ns == 46 * NS
    assert frozen.expires_at_monotonic_ns == 46 * NS
    assert {row["origin"] for row in frozen.manifest["destinations"]} == {
        "https://" + HOST
    }
    assert all(call[3:] == (hr.QUERY_DEADLINE_S, 21 * NS) for call in tcp.calls)


@pytest.mark.parametrize("ttl", [0, 44])
@pytest.mark.parametrize("short_record", ["cname", "a", "aaaa"])
def test_real_resolver_refuses_short_ttl_on_every_relevant_record(
    monkeypatch: pytest.MonkeyPatch, ttl: int, short_record: str
) -> None:
    def zone(resolver: str, host: str, qtype: int) -> list[Record]:
        rows = records(host, qtype)
        index = 0 if short_record == "cname" else 1
        if short_record == "cname" or short_record == (
            "a" if qtype == hr.TYPE_A else "aaaa"
        ):
            owner, rtype, _, value = rows[index]
            rows[index] = (owner, rtype, ttl, value)
        return rows

    clock, _ = install(monkeypatch, zone)
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.dns$"):
        cb.capture(config(), deadline_ns=10 * NS, clock_ns=clock)


@pytest.mark.parametrize(
    "qtype,address",
    [
        (hr.TYPE_A, "10.0.0.1"),
        (hr.TYPE_AAAA, "fd00::1"),
        (hr.TYPE_A, "192.0.2.10"),
        (hr.TYPE_AAAA, "2001:db8::10"),
    ],
)
def test_capture_default_routability_refuses_private_and_documentation_wire(
    monkeypatch: pytest.MonkeyPatch, qtype: int, address: str
) -> None:
    clock, _ = install(
        monkeypatch,
        lambda resolver, host, family: records(
            host, family, address=address if family == qtype else None
        ),
    )
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.dns$"):
        cb.capture(config(), deadline_ns=10 * NS, clock_ns=clock)


@pytest.mark.parametrize(
    "bad_chain", ["unapproved", "loop", "family_mismatch", "other_origin_alias"]
)
def test_capture_refuses_wrong_cname_chain_through_real_resolver(
    monkeypatch: pytest.MonkeyPatch, bad_chain: str
) -> None:
    def zone(resolver: str, host: str, qtype: int) -> list[Record]:
        if bad_chain == "loop":
            return [(host, hr.TYPE_CNAME, 45, EDGE), (EDGE, hr.TYPE_CNAME, 45, host)]
        alias = EDGE
        if bad_chain in ("unapproved", "other_origin_alias"):
            alias = "other-edge.example.net"
        if bad_chain == "family_mismatch" and qtype == hr.TYPE_AAAA:
            return [(host, qtype, 45, V6)]
        return records(host, qtype, alias=alias)

    clock, _ = install(monkeypatch, zone)
    candidate = (
        config((HOST, (EDGE,)), ("other.example", ("other-edge.example.net",)))
        if bad_chain == "other_origin_alias"
        else config()
    )
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.dns$"):
        cb.capture(candidate, deadline_ns=10 * NS, clock_ns=clock)


@pytest.mark.parametrize("disagreement", ["chain", "a", "aaaa"])
def test_capture_refuses_dual_resolver_disagreement(
    monkeypatch: pytest.MonkeyPatch, disagreement: str
) -> None:
    def zone(resolver: str, host: str, qtype: int) -> list[Record]:
        alias, address = EDGE, None
        if resolver == RESOLVERS[1]:
            if disagreement == "chain":
                alias = "alternative.example.net"
            elif disagreement == "a" and qtype == hr.TYPE_A:
                address = "1.1.1.1"
            elif disagreement == "aaaa" and qtype == hr.TYPE_AAAA:
                address = "2606:4700::2"
        return records(host, qtype, alias=alias, address=address)

    clock, tcp = install(monkeypatch, zone)
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.dns_agreement$"):
        cb.capture(
            config((HOST, (EDGE, "alternative.example.net"))),
            deadline_ns=10 * NS,
            clock_ns=clock,
        )
    assert len(tcp.calls) == 4


def test_capture_tcp_queries_inherit_one_shrinking_absolute_budget_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, tcp = install(
        monkeypatch,
        lambda resolver, host, qtype: records(host, qtype),
        advance_ns=NS // 4,
    )
    cb.capture(config(), deadline_ns=3 * NS, clock_ns=clock)
    assert [(call[3], call[4]) for call in tcp.calls] == [
        (2.0, 3 * NS),
        (1.75, 3 * NS),
        (1.5, 3 * NS),
        (1.25, 3 * NS),
    ]
    assert Counter((r, h, q) for r, h, q, _, _ in tcp.calls) == Counter(
        (resolver, HOST, qtype)
        for resolver in RESOLVERS
        for qtype in (hr.TYPE_A, hr.TYPE_AAAA)
    )


def test_capture_refuses_when_exchange_returns_after_inherited_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, tcp = install(
        monkeypatch,
        lambda resolver, host, qtype: records(host, qtype),
        advance_ns=2 * NS,
    )
    with pytest.raises(cb.BootstrapRefused):
        cb.capture(config(), deadline_ns=2 * NS, clock_ns=clock)
    # Late but well-formed A bytes cannot start AAAA or reset the budget.
    assert len(tcp.calls) == 1
    assert tcp.calls[0] == (RESOLVERS[0], HOST, hr.TYPE_A, 1.0, 2 * NS)


def test_expired_capture_never_enters_tcp_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, tcp = install(
        monkeypatch, lambda resolver, host, qtype: records(host, qtype)
    )
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.deadline$"):
        cb.capture(config(), deadline_ns=NS, clock_ns=clock)
    assert tcp.calls == []


def test_failed_tcp_exchange_is_not_retried_or_fallen_back_to_other_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(resolver: str, host: str, qtype: int) -> list[Record]:
        raise hr.SnapshotRefused("dns.timeout")

    clock, tcp = install(monkeypatch, timeout)
    with pytest.raises(cb.BootstrapRefused, match="^bootstrap.dns$"):
        cb.capture(config(), deadline_ns=10 * NS, clock_ns=clock)
    counts = Counter((r, h, q) for r, h, q, _, _ in tcp.calls)
    assert counts
    assert all(
        qtype == hr.TYPE_A and count == 1 for (_, _, qtype), count in counts.items()
    )
    assert all(call[4] == 10 * NS for call in tcp.calls)


@pytest.mark.parametrize("connect_elapsed_s", [0.5, 2.0])
def test_canonical_tcp_owner_never_resets_absolute_deadline_after_connect(
    monkeypatch: pytest.MonkeyPatch, connect_elapsed_s: float
) -> None:
    """Keep the real TCP owner and recv loop; inject only its socket and clock."""
    now = [1.0]
    sockets = []
    payload = bytes(12)

    class Socket:
        def __init__(self, family: int, kind: int) -> None:
            assert (family, kind) == (hr.socket.AF_INET, hr.socket.SOCK_STREAM)
            self.timeouts: list[float] = []
            self.connections: list[tuple[str, int]] = []
            self.sent: list[bytes] = []
            self.received = 0
            self.closed = False
            sockets.append(self)

        def __enter__(self) -> Socket:
            return self

        def __exit__(self, *args: object) -> None:
            self.closed = True

        def settimeout(self, seconds: float) -> None:
            self.timeouts.append(seconds)

        def connect(self, destination: tuple[str, int]) -> None:
            self.connections.append(destination)
            now[0] += connect_elapsed_s

        def sendall(self, value: bytes) -> None:
            self.sent.append(value)
            now[0] += 0.25

        def recv(self, count: int) -> bytes:
            self.received += 1
            now[0] += 0.25
            if self.received == 1:
                assert count == 2
                return struct.pack(">H", len(payload))
            assert count == len(payload)
            return payload

    monkeypatch.setattr(hr.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(hr.socket, "socket", Socket)
    exchange = hr.tcp_exchange(RESOLVERS[0], deadline_s=5.0, deadline_ns=3 * NS)
    # Time between factory creation and invocation belongs to the same budget.
    now[0] = 1.25
    query = hr.encode_query(7, HOST, hr.TYPE_A)
    if connect_elapsed_s == 2.0:
        with pytest.raises(hr.SnapshotRefused) as caught:
            exchange(query)
        assert caught.value.label == "dns.timeout"
    else:
        assert exchange(query) == payload
    assert len(sockets) == 1
    sock = sockets[0]
    assert sock.connections == [(RESOLVERS[0], 53)]
    assert sock.closed
    if connect_elapsed_s == 2.0:
        # A connect completing late must not send, receive, reconnect or retry.
        assert sock.timeouts == [1.75]
        assert sock.sent == []
        assert sock.received == 0
    else:
        assert sock.timeouts == [1.75, 1.25, 1.0, 0.75]
        assert sock.sent == [struct.pack(">H", len(query)) + query]
        assert sock.received == 2
