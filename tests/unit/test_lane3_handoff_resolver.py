"""DNS snapshot canaries for the Lane 3 broker handoff; hosted CI only.

A synthetic in-memory resolver answers crafted DNS messages. The globally
routable fixtures below are synthetic stand-ins injected through the
``routable`` seam ONLY where a positive path needs an address; the default
predicate is separately proven to refuse the documentation ranges, private,
loopback, link-local and embedded-IPv4 forms.
"""

from __future__ import annotations

import ipaddress
import pathlib
import struct
import sys
from collections.abc import Callable
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_handoff_resolver as hr

HOST = "broker.example"
ORIGIN = "https://" + HOST
ALIASES = hr.AliasPolicy(frozenset({"edge.example.net"}), frozenset({"cdn.example.org"}))
DOC = tuple(
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)


def documentation_only(address: Any) -> bool:
    """Test seam: treat ONLY documentation ranges as routable."""
    return any(address in n for n in DOC if n.version == address.version)


def _name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def message(
    qid: int,
    qname: str,
    qtype: int,
    answers: list[tuple[str, int, int, str]],
    rcode: int = 0,
    flags: int = 0x8180,
) -> bytes:
    out = struct.pack(">HHHHHH", qid, flags | rcode, 1, len(answers), 0, 0)
    out += _name(qname) + struct.pack(">HH", qtype, 1)
    for owner, rtype, ttl, value in answers:
        if rtype == hr.TYPE_A:
            data = ipaddress.IPv4Address(value).packed
        elif rtype == hr.TYPE_AAAA:
            data = ipaddress.IPv6Address(value).packed
        else:
            data = _name(value)
        out += _name(owner) + struct.pack(">HHIH", rtype, 1, ttl, len(data)) + data
    return out


Zone = dict[int, tuple[list[tuple[str, int, int, str]], int]]


def server(zone: Zone) -> Callable[[bytes], bytes]:
    def exchange(query: bytes) -> bytes:
        qid = struct.unpack(">H", query[:2])[0]
        qtype = struct.unpack(">H", query[-4:-2])[0]
        answers, rcode = zone.get(qtype, ([], 0))
        return message(qid, HOST, qtype, answers, rcode)

    return exchange


def run(zone: Zone, **kwargs: Any) -> hr.Snapshot:
    args: dict[str, Any] = {
        "resolver": "127.0.0.53",
        "aliases": ALIASES,
        "max_addresses": 32,
        "exchange": server(zone),
        "clock_ns": lambda: 1_000_000_000,
        "routable": documentation_only,
    }
    args.update(kwargs)
    return hr.resolve(ORIGIN, **args)


GOOD: Zone = {
    hr.TYPE_A: ([(HOST, hr.TYPE_A, 300, "192.0.2.10")], 0),
    hr.TYPE_AAAA: ([(HOST, hr.TYPE_AAAA, 300, "2001:db8::10")], 0),
}


def refusal(zone: Zone, **kwargs: Any) -> str:
    with pytest.raises(hr.SnapshotRefused) as caught:
        run(zone, **kwargs)
    return caught.value.label


def test_positive_snapshot_is_bounded_and_digested() -> None:
    snap = run(GOOD)
    assert snap.rows() == [
        {"family": 4, "address": "192.0.2.10"},
        {"family": 6, "address": "2001:db8::10"},
    ]
    assert snap.expires_at_monotonic_ns == 1_000_000_000 + 60 * 1_000_000_000
    assert snap.public()["port"] == 443
    assert len(snap.digest) == 64
    assert run(GOOD).digest == snap.digest


def test_cname_chain_inside_alias_policy_is_accepted() -> None:
    chain = [
        (HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
        ("edge.example.net", hr.TYPE_CNAME, 300, "a.cdn.example.org"),
    ]
    zone: Zone = {
        hr.TYPE_A: ([*chain, ("a.cdn.example.org", hr.TYPE_A, 120, "192.0.2.10")], 0),
        hr.TYPE_AAAA: (list(chain), 0),
    }
    snap = run(zone)
    assert snap.chain == (HOST, "edge.example.net", "a.cdn.example.org")
    assert snap.ttl_s == 120


def test_unapproved_alias_refuses_and_namespace_is_label_bounded() -> None:
    for target in ("other.example.net", "badcdn.example.org", "cdn.example.org"):
        zone: Zone = {
            hr.TYPE_A: (
                [
                    (HOST, hr.TYPE_CNAME, 300, target),
                    (target, hr.TYPE_A, 300, "192.0.2.10"),
                ],
                0,
            ),
            hr.TYPE_AAAA: ([(HOST, hr.TYPE_CNAME, 300, target)], 0),
        }
        assert refusal(zone) == "dns.alias_not_admitted"
    # Sensitivity: the label-bounded member is admitted.
    assert ALIASES.admits("x.cdn.example.org")


def test_cname_loop_refuses() -> None:
    zone: Zone = {
        hr.TYPE_A: (
            [
                (HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
                ("edge.example.net", hr.TYPE_CNAME, 300, HOST),
            ],
            0,
        ),
        hr.TYPE_AAAA: ([], 0),
    }
    assert refusal(zone) == "dns.cname_loop"


def test_chain_longer_than_eight_refuses_and_eight_is_accepted() -> None:
    def zone_of(links: int) -> Zone:
        names = [HOST] + [f"n{i}.cdn.example.org" for i in range(links)]
        rows = [
            (names[i], hr.TYPE_CNAME, 300, names[i + 1]) for i in range(links)
        ]
        return {
            hr.TYPE_A: ([*rows, (names[-1], hr.TYPE_A, 300, "192.0.2.10")], 0),
            hr.TYPE_AAAA: (list(rows), 0),
        }

    assert len(run(zone_of(hr.MAX_CHAIN)).chain) == hr.MAX_CHAIN + 1
    assert refusal(zone_of(hr.MAX_CHAIN + 1)) == "dns.cname_chain"


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "192.0.2.10",
    ],
)
def test_default_predicate_refuses_non_global_v4(address: str) -> None:
    assert not hr.globally_routable(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "2002:808:808::1",
        "64:ff9b::808:808",
        "2001::1",
        "2001:db8::1",
        "fec0::1",
    ],
)
def test_default_predicate_refuses_non_global_and_embedded_v6(address: str) -> None:
    assert not hr.globally_routable(ipaddress.ip_address(address))


def test_default_predicate_accepts_ordinary_global_unicast() -> None:
    # Sensitivity for the two tests above: an ordinary global address passes.
    assert hr.globally_routable(ipaddress.ip_address("8.8.8.8"))
    assert hr.globally_routable(ipaddress.ip_address("2606:4700::1"))


def test_mixed_public_private_answer_refuses_whole_set() -> None:
    zone: Zone = {
        hr.TYPE_A: (
            [
                (HOST, hr.TYPE_A, 300, "192.0.2.10"),
                (HOST, hr.TYPE_A, 300, "10.0.0.1"),
            ],
            0,
        ),
        hr.TYPE_AAAA: ([], 0),
    }
    assert refusal(zone) == "dns.address_not_routable"


def test_default_predicate_refuses_documentation_answers() -> None:
    assert refusal(GOOD, routable=hr.globally_routable) == "dns.address_not_routable"


def test_address_overflow_refuses_without_truncation() -> None:
    rows = [(HOST, hr.TYPE_A, 300, f"198.51.100.{i}") for i in range(1, 5)]
    zone: Zone = {hr.TYPE_A: (rows, 0), hr.TYPE_AAAA: ([], 0)}
    assert len(run(zone, max_addresses=4).rows()) == 4
    assert refusal(zone, max_addresses=3) == "dns.address_overflow"


def test_short_ttl_refuses_and_threshold_is_exact() -> None:
    def zone_ttl(ttl: int) -> Zone:
        return {
            hr.TYPE_A: ([(HOST, hr.TYPE_A, ttl, "192.0.2.10")], 0),
            hr.TYPE_AAAA: ([], 0),
        }

    assert run(zone_ttl(hr.MIN_TTL_S)).ttl_s == hr.MIN_TTL_S
    assert refusal(zone_ttl(hr.MIN_TTL_S - 1)) == "dns.short_ttl"


@pytest.mark.parametrize("rcode", [2, 3, 5])
def test_partial_failure_refuses(rcode: int) -> None:
    zone: Zone = {
        hr.TYPE_A: ([(HOST, hr.TYPE_A, 300, "192.0.2.10")], 0),
        hr.TYPE_AAAA: ([], rcode),
    }
    assert refusal(zone) == "dns.failure"


def test_off_chain_record_and_cname_beside_data_refuse() -> None:
    off: Zone = {
        hr.TYPE_A: (
            [
                (HOST, hr.TYPE_A, 300, "192.0.2.10"),
                ("other.example", hr.TYPE_A, 300, "192.0.2.11"),
            ],
            0,
        ),
        hr.TYPE_AAAA: ([], 0),
    }
    assert refusal(off) == "dns.ambiguous"
    beside: Zone = {
        hr.TYPE_A: (
            [
                (HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
                (HOST, hr.TYPE_A, 300, "192.0.2.10"),
            ],
            0,
        ),
        hr.TYPE_AAAA: ([], 0),
    }
    assert refusal(beside) == "dns.ambiguous"


def test_families_on_different_chains_refuse() -> None:
    zone: Zone = {
        hr.TYPE_A: (
            [
                (HOST, hr.TYPE_CNAME, 300, "edge.example.net"),
                ("edge.example.net", hr.TYPE_A, 300, "192.0.2.10"),
            ],
            0,
        ),
        hr.TYPE_AAAA: (
            [
                (HOST, hr.TYPE_CNAME, 300, "a.cdn.example.org"),
                ("a.cdn.example.org", hr.TYPE_AAAA, 300, "2001:db8::10"),
            ],
            0,
        ),
    }
    assert refusal(zone) == "dns.ambiguous"


def test_rebinding_changes_the_snapshot_digest() -> None:
    first = run(GOOD)
    rebound: Zone = {
        hr.TYPE_A: ([(HOST, hr.TYPE_A, 300, "203.0.113.10")], 0),
        hr.TYPE_AAAA: GOOD[hr.TYPE_AAAA],
    }
    second = run(rebound)
    assert first.digest != second.digest
    assert first.rows() != second.rows()


def test_malformed_and_mismatched_responses_refuse() -> None:
    def broken(query: bytes) -> bytes:
        qid = struct.unpack(">H", query[:2])[0]
        return message(qid ^ 1, HOST, hr.TYPE_A, [])

    assert refusal(GOOD, exchange=broken) == "dns.malformed"

    def truncated(query: bytes) -> bytes:
        qid = struct.unpack(">H", query[:2])[0]
        return message(qid, HOST, hr.TYPE_A, [], flags=0x8380)

    assert refusal(GOOD, exchange=truncated) == "dns.truncated"

    def short(query: bytes) -> bytes:
        return query[:5]

    assert refusal(GOOD, exchange=short) == "dns.malformed"

    def failing(query: bytes) -> bytes:
        raise OSError("synthetic")

    assert refusal(GOOD, exchange=failing) == "dns.failure"


def test_snapshot_rows_are_rechecked_where_consumed() -> None:
    rows = [{"family": 4, "address": "192.0.2.10"}]
    assert hr.validate_snapshot_addresses(rows, documentation_only) == rows
    for bad in (
        [{"family": 4, "address": "10.0.0.1"}],
        [{"family": 4, "address": "192.0.2.10"}] * 2,
        [{"family": 6, "address": "2001:db8::2"}, {"family": 4, "address": "192.0.2.1"}],
        [],
    ):
        with pytest.raises(hr.SnapshotRefused):
            hr.validate_snapshot_addresses(bad, documentation_only)
    with pytest.raises(hr.SnapshotRefused):
        hr.validate_snapshot_addresses(rows)  # default predicate refuses docs


def test_refused_origin_is_never_resolved() -> None:
    calls: list[bytes] = []

    def exchange(query: bytes) -> bytes:
        calls.append(query)
        return b""

    for origin in ("https://broker.example:443", "https://u@broker.example"):
        with pytest.raises(hr.SnapshotRefused):
            hr.resolve(
                origin,
                resolver="127.0.0.53",
                aliases=ALIASES,
                max_addresses=32,
                exchange=exchange,
            )
    assert calls == []
