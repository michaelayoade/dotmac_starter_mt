"""Design row "DNS/network" (hosted isolated Linux namespace).

A synthetic DNS server listens over TCP inside the namespace. CNAME loops,
unapproved aliases, private/mixed answers, address overflow, short TTL and
rebinding fail; the numeric connection and the firewall consume the
IDENTICAL snapshot while SNI/Host authenticate the original host.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import struct
import threading
from collections.abc import Iterator
from typing import Any

import lane3_handoff_protocol as hp
import lane3_handoff_resolver as hr
import pytest
from conftest import (
    BROKER,
    BROKER_HOST,
    OTHER_ADDRESS,
    PLAIN_ADDRESS,
    TLS_ADDRESS,
    Env,
    documentation_routable,
    owned_rules,
)

RESOLVER = "127.0.0.53"
ALIASES = hr.AliasPolicy(frozenset({"edge.example.net"}), frozenset())


def _name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


class DnsServer:
    """TCP-only synthetic resolver; ``zone[qtype]`` lists answer records."""

    def __init__(self) -> None:
        self.zone: dict[int, list[tuple[str, int, int, str]]] = {}
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((RESOLVER, 53))
        self.server.listen(8)
        self.server.settimeout(0.2)
        self.stopped = False
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self) -> None:
        while not self.stopped:
            try:
                conn, _ = self.server.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(5)
                length = struct.unpack(">H", conn.recv(2))[0]
                query = b""
                while len(query) < length:
                    query += conn.recv(length - len(query))
                qid = struct.unpack(">H", query[:2])[0]
                qtype = struct.unpack(">H", query[-4:-2])[0]
                answers = self.zone.get(qtype, [])
                out = struct.pack(">HHHHHH", qid, 0x8180, 1, len(answers), 0, 0)
                out += _name(BROKER_HOST) + struct.pack(">HH", qtype, 1)
                for owner, rtype, ttl, value in answers:
                    if rtype == hr.TYPE_A:
                        data = ipaddress.IPv4Address(value).packed
                    elif rtype == hr.TYPE_AAAA:
                        data = ipaddress.IPv6Address(value).packed
                    else:
                        data = _name(value)
                    out += _name(owner) + struct.pack(">HHIH", rtype, 1, ttl, len(data))
                    out += data
                conn.sendall(struct.pack(">H", len(out)) + out)

    def stop(self) -> None:
        self.stopped = True
        self.server.close()


@pytest.fixture(scope="session")
def dns_server(network: Any) -> Iterator[DnsServer]:
    server = DnsServer()
    yield server
    server.stop()


@pytest.fixture()
def dns(dns_server: DnsServer) -> DnsServer:
    dns_server.zone = {}
    return dns_server


def resolve(**kwargs: Any) -> hr.Snapshot:
    args: dict[str, Any] = {
        "resolver": RESOLVER,
        "aliases": ALIASES,
        "max_addresses": 32,
        "routable": documentation_routable,
    }
    args.update(kwargs)
    return hr.resolve(BROKER, **args)


def a(
    address: str, ttl: int = 300, owner: str = BROKER_HOST
) -> tuple[str, int, int, str]:
    return (owner, hr.TYPE_A, ttl, address)


@pytest.mark.parametrize(
    ("zone", "label"),
    [
        (
            {
                hr.TYPE_A: [
                    (BROKER_HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
                    ("edge.example.net", hr.TYPE_CNAME, 300, BROKER_HOST),
                ]
            },
            "dns.cname_loop",
        ),
        (
            {
                hr.TYPE_A: [
                    (BROKER_HOST, hr.TYPE_CNAME, 300, "rogue.example.net"),
                    a(TLS_ADDRESS, owner="rogue.example.net"),
                ]
            },
            "dns.alias_not_admitted",
        ),
        ({hr.TYPE_A: [a(TLS_ADDRESS), a("10.1.2.3")]}, "dns.address_not_routable"),
        (
            {hr.TYPE_AAAA: [(BROKER_HOST, hr.TYPE_AAAA, 300, "fd00::1")]},
            "dns.address_not_routable",
        ),
        (
            {hr.TYPE_A: [a(f"203.0.113.{i}") for i in range(1, 34)]},
            "dns.address_overflow",
        ),
        ({hr.TYPE_A: [a(TLS_ADDRESS, ttl=10)]}, "dns.short_ttl"),
        ({}, "dns.no_address"),
    ],
)
def test_real_tcp_resolution_refusals(dns: DnsServer, zone: dict, label: str) -> None:
    dns.zone = zone
    with pytest.raises(hr.SnapshotRefused) as caught:
        resolve()
    assert caught.value.label == label


def test_real_tcp_resolution_positive(dns: DnsServer) -> None:
    dns.zone = {
        hr.TYPE_A: [
            (BROKER_HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
            a(TLS_ADDRESS, owner="edge.example.net"),
        ],
        hr.TYPE_AAAA: [(BROKER_HOST, hr.TYPE_CNAME, 300, "edge.example.net")],
    }
    snap = resolve()
    assert snap.rows() == [{"family": 4, "address": TLS_ADDRESS}]
    assert snap.chain == (BROKER_HOST, "edge.example.net")
    # The default predicate refuses the same documentation answer.
    with pytest.raises(hr.SnapshotRefused):
        resolve(routable=hr.globally_routable)


def test_firewall_and_connection_consume_the_identical_snapshot(
    env: Env, dns: DnsServer, network
) -> None:
    dns.zone = {hr.TYPE_A: [a(TLS_ADDRESS)]}
    snap = resolve()
    token_requests = network.tls_accepts
    lease = env.start(["obtain", "tls", OTHER_ADDRESS])
    env.wait_state(lease, {"REPORTED"})
    assert network.tls_accepts == token_requests, "no token request before grant"
    reply = env.grant(
        lease,
        snap.rows(),
        snapshot={
            "digest": snap.digest,
            "ttl_remaining_ms": 60_000,
            "addresses": snap.rows(),
        },
    )
    assert reply["state"] == "GRANTED"
    # Rebinding after the snapshot: DNS now answers another address.
    dns.zone = {hr.TYPE_A: [a(OTHER_ADDRESS)]}
    rebound = resolve()
    assert rebound.digest != snap.digest
    out = env.job_output()
    # The job used the snapshot address, TLS authenticated the original host
    # (wrong SNI refused) and the rebound address stays blocked.
    assert out["address"] == TLS_ADDRESS
    assert out["tls"] == "ok" and out["wrong_sni"] == "tls.refused"
    assert out["wrong_ca"] == "tls.refused"
    assert out["stale"] == "tls.refused"
    assert out["delayed"] == "tls.refused"
    assert out["other"] == "blocked"
    j = env.wait_state(lease, {"CONSUMED"})
    assert j["grant"]["snapshot"]["digest"] == snap.digest
    granted = {
        e["match"]["right"]
        for r in owned_rules()
        for e in r["expr"]
        if "match" in e
        and e["match"]["left"].get("payload", {}).get("field") == "daddr"
    }
    assert TLS_ADDRESS in granted and OTHER_ADDRESS not in granted
    public = env.cleanup(lease)["evidence"]
    assert hp.validate_evidence(public) == public
    assert public["token_request_started"] is True
    assert public["issuance"] == "UNKNOWN"
    assert public["proof_outcome"] == "BOUNDED_PROOF"
    assert "synthetic-request-canary" not in json.dumps(public)
    assert "synthetic-batch-canary" not in json.dumps(public)
    env.assert_clean(lease)


def test_production_consumer_handshake_stall_is_bounded(env: Env):
    # Plain server accepts TCP and never completes TLS. No credential-bearing
    # HTTP request can be sent before a successful authenticated handshake.
    lease = env.start(["obtain", "handshake"])
    env.grant(lease, [{"family": 4, "address": PLAIN_ADDRESS}])
    out = env.job_output()
    assert out["tls"] == "tls.refused"
    assert out["elapsed_ms"] < 1500
    public = env.cleanup(lease)["evidence"]
    assert public["token_request_started"] is False
    assert public["issuance"] == "NOT_STARTED"
    assert public["proof_outcome"] == "UNKNOWN"
    env.assert_clean(lease)
