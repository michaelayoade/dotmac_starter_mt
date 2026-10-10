"""One trusted DNS snapshot for an approved broker origin; fail-closed.

Resolution belongs to the trusted coordinator/controller, never to a
runner-provided address list. This module asks ONE configured recursive
resolver, over TCP (no UDP spoofing window, no truncation fallback), for the A
and AAAA sets of the approved host and builds a snapshot:

* the CNAME chain is at most eight links, loop-free, and every alias target is
  inside the independently reviewed alias policy (exact names, or reviewed
  namespaces matched on a label boundary). A CNAME never changes the
  authorized HTTP origin;
* both families follow the identical chain; any record owned by a name off the
  chain, a CNAME beside data, a partial failure (one family NXDOMAIN/SERVFAIL)
  or a malformed message refuses;
* every address is globally routable unicast. Private, loopback, link-local
  (metadata), unspecified, multicast, reserved and embedded-IPv4 forms
  (IPv4-mapped, 6to4, NAT64, Teredo) refuse the WHOLE answer, so a mixed
  public/private set cannot pass;
* the address count is bounded by the caller's remaining controller capacity
  (the controller holds at most 32 exact addresses). An answer that does not
  fit refuses; it is never silently truncated;
* the snapshot lives for min(smallest TTL, 60 s) from capture, and a TTL below
  the grant margin refuses.

Refusals are :class:`SnapshotRefused` with a fixed label; no name, address or
response text is ever placed in a message.

Python standard library only.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import struct
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lane3_handoff_protocol as hp

MAX_CHAIN: Final = 8
MIN_TTL_S: Final = 45
MAX_SNAPSHOT_S: Final = 60
QUERY_DEADLINE_S: Final = 5.0
MAX_DNS_MESSAGE: Final = 65535
TYPE_A: Final = 1
TYPE_CNAME: Final = 5
TYPE_AAAA: Final = 28
CLASS_IN: Final = 1
_LABEL: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

#: Embedded-IPv4 / translation prefixes whose address is not the real peer.
_BYPASS_V6: Final = tuple(
    ipaddress.ip_network(n)
    for n in ("2002::/16", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32")
)


class SnapshotRefused(RuntimeError):
    """A fixed-label refusal; ``label`` is never input text."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label

    def __str__(self) -> str:
        return self.label


def globally_routable(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Globally routable unicast and not an embedded-IPv4 bypass form."""
    if (
        not address.is_global
        or address.is_multicast
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if (
            address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or address.is_site_local
            or any(address in network for network in _BYPASS_V6)
        ):
            return False
    return True


def valid_hostname(name: str) -> bool:
    if type(name) is not str or not name or len(name) > 253 or name.endswith("."):
        return False
    labels = name.split(".")
    return len(labels) >= 2 and all(
        _LABEL.fullmatch(label) is not None for label in labels
    )


@dataclass(frozen=True)
class AliasPolicy:
    """Independently reviewed CNAME targets: exact names and namespaces.

    A namespace ``example.net`` admits ``a.example.net`` (label boundary), never
    ``badexample.net`` or ``example.net`` itself unless listed exactly.
    """

    exact: frozenset[str] = field(default_factory=frozenset)
    namespaces: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AliasPolicy:
        if not isinstance(value, Mapping) or set(value) != {"exact", "namespaces"}:
            raise SnapshotRefused("alias.policy")
        exact, spaces = value["exact"], value["namespaces"]
        if type(exact) is not list or type(spaces) is not list:
            raise SnapshotRefused("alias.policy")
        if len(exact) + len(spaces) > 64:
            raise SnapshotRefused("alias.policy")
        for name in [*exact, *spaces]:
            if not valid_hostname(name):
                raise SnapshotRefused("alias.policy")
        if len(set(exact)) != len(exact) or len(set(spaces)) != len(spaces):
            raise SnapshotRefused("alias.policy")
        return cls(frozenset(exact), frozenset(spaces))

    def admits(self, name: str) -> bool:
        if name in self.exact:
            return True
        return any(name.endswith("." + space) for space in self.namespaces)

    def as_mapping(self) -> dict[str, list[str]]:
        return {"exact": sorted(self.exact), "namespaces": sorted(self.namespaces)}


# ── DNS wire format (queries and strict response parsing) ──────────────────


def encode_query(query_id: int, name: str, qtype: int) -> bytes:
    if not valid_hostname(name):
        raise SnapshotRefused("dns.name")
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in name.split(".")
    )
    return header + qname + b"\x00" + struct.pack(">HH", qtype, CLASS_IN)


def _read_name(message: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumps = 0
    end: int | None = None
    total = 0
    while True:
        if offset >= len(message):
            raise SnapshotRefused("dns.malformed")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise SnapshotRefused("dns.malformed")
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            if end is None:
                end = offset + 2
            jumps += 1
            if jumps > 32 or pointer >= offset:
                raise SnapshotRefused("dns.malformed")
            offset = pointer
            continue
        if length & 0xC0:
            raise SnapshotRefused("dns.malformed")
        offset += 1
        if length == 0:
            break
        if offset + length > len(message):
            raise SnapshotRefused("dns.malformed")
        raw = message[offset : offset + length]
        offset += length
        try:
            label = raw.decode("ascii")
        except UnicodeDecodeError:
            raise SnapshotRefused("dns.malformed") from None
        labels.append(label.lower())
        total += length + 1
        if total > 254:
            raise SnapshotRefused("dns.malformed")
    return ".".join(labels), (end if end is not None else offset)


@dataclass(frozen=True)
class Record:
    owner: str
    rtype: int
    ttl: int
    value: str


@dataclass(frozen=True)
class Answer:
    rcode: int
    records: tuple[Record, ...]


def parse_response(message: bytes, query_id: int, name: str, qtype: int) -> Answer:
    """Strictly parse one response to exactly the question we asked."""
    if len(message) < 12:
        raise SnapshotRefused("dns.malformed")
    rid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", message[:12])
    if rid != query_id or not flags & 0x8000 or (flags >> 11) & 0xF:
        raise SnapshotRefused("dns.malformed")
    if flags & 0x0200:
        raise SnapshotRefused("dns.truncated")
    if qd != 1:
        raise SnapshotRefused("dns.malformed")
    rcode = flags & 0xF
    qname, offset = _read_name(message, 12)
    if offset + 4 > len(message):
        raise SnapshotRefused("dns.malformed")
    qt, qc = struct.unpack(">HH", message[offset : offset + 4])
    offset += 4
    if qname != name or qt != qtype or qc != CLASS_IN:
        raise SnapshotRefused("dns.malformed")
    records: list[Record] = []
    for _ in range(an):
        owner, offset = _read_name(message, offset)
        if offset + 10 > len(message):
            raise SnapshotRefused("dns.malformed")
        rtype, rclass, ttl, length = struct.unpack(
            ">HHIH", message[offset : offset + 10]
        )
        offset += 10
        if offset + length > len(message) or rclass != CLASS_IN:
            raise SnapshotRefused("dns.malformed")
        data_start = offset
        offset += length
        if rtype == TYPE_A:
            if length != 4:
                raise SnapshotRefused("dns.malformed")
            value = str(ipaddress.IPv4Address(message[data_start:offset]))
        elif rtype == TYPE_AAAA:
            if length != 16:
                raise SnapshotRefused("dns.malformed")
            value = str(ipaddress.IPv6Address(message[data_start:offset]))
        elif rtype == TYPE_CNAME:
            value, used = _read_name(message, data_start)
            if used != offset:
                raise SnapshotRefused("dns.malformed")
        else:
            # Any other record type in the answer section is ambiguous here.
            raise SnapshotRefused("dns.ambiguous")
        if ttl > 0x7FFFFFFF:
            raise SnapshotRefused("dns.malformed")
        records.append(Record(owner, rtype, ttl, value))
    # Authority/additional sections are ignored; they are never authority here.
    del ns, ar
    return Answer(rcode, tuple(records))


Exchange = Callable[[bytes], bytes]


def tcp_exchange(
    resolver: str, port: int = 53, deadline_s: float = QUERY_DEADLINE_S
) -> Exchange:
    """One DNS-over-TCP exchange per query to ONE numeric resolver address."""
    address = ipaddress.ip_address(resolver)
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6

    def exchange(query: bytes) -> bytes:
        deadline = time.monotonic() + deadline_s
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(deadline_s)
            sock.connect((str(address), port))
            sock.sendall(struct.pack(">H", len(query)) + query)
            header = _recv(sock, 2, deadline)
            (length,) = struct.unpack(">H", header)
            if not 12 <= length <= MAX_DNS_MESSAGE:
                raise SnapshotRefused("dns.malformed")
            return _recv(sock, length, deadline)

    return exchange


def _recv(sock: socket.socket, count: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    while count:
        left = deadline - time.monotonic()
        if left <= 0:
            raise SnapshotRefused("dns.timeout")
        sock.settimeout(left)
        chunk = sock.recv(min(count, 65536))
        if not chunk:
            raise SnapshotRefused("dns.malformed")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


# ── snapshot construction ──────────────────────────────────────────────────


def _family_result(
    host: str, answer: Answer, rtype: int, aliases: AliasPolicy
) -> tuple[list[str], list[str], int | None]:
    """(chain, addresses, min ttl) for one family; NODATA gives no addresses."""
    if answer.rcode != 0:
        raise SnapshotRefused("dns.failure")
    by_owner: dict[str, list[Record]] = {}
    for record in answer.records:
        by_owner.setdefault(record.owner, []).append(record)
    chain = [host]
    seen = {host}
    ttls: list[int] = []
    current = host
    while True:
        rows = by_owner.pop(current, [])
        cnames = [r for r in rows if r.rtype == TYPE_CNAME]
        data = [r for r in rows if r.rtype != TYPE_CNAME]
        if cnames and data:
            raise SnapshotRefused("dns.ambiguous")
        if len(cnames) > 1:
            raise SnapshotRefused("dns.ambiguous")
        if not cnames:
            break
        target = cnames[0].value
        ttls.append(cnames[0].ttl)
        if target in seen:
            raise SnapshotRefused("dns.cname_loop")
        if len(chain) - 1 >= MAX_CHAIN:
            raise SnapshotRefused("dns.cname_chain")
        if not valid_hostname(target) or not aliases.admits(target):
            raise SnapshotRefused("dns.alias_not_admitted")
        chain.append(target)
        seen.add(target)
        current = target
    if by_owner:
        # Records for a name that is not on the chain: ambiguous answer.
        raise SnapshotRefused("dns.ambiguous")
    addresses: list[str] = []
    for record in data:
        if record.rtype != rtype:
            raise SnapshotRefused("dns.ambiguous")
        ttls.append(record.ttl)
        addresses.append(record.value)
    if len(set(addresses)) != len(addresses):
        raise SnapshotRefused("dns.ambiguous")
    return chain, addresses, (min(ttls) if ttls else None)


@dataclass(frozen=True)
class Snapshot:
    """A private snapshot. ``public()`` is the grant's ``snapshot`` object."""

    origin: str
    resolver: str
    chain: tuple[str, ...]
    addresses: tuple[tuple[int, str], ...]
    ttl_s: int
    captured_at_monotonic_ns: int
    expires_at_monotonic_ns: int
    digest: str

    def rows(self) -> list[dict[str, Any]]:
        return [{"family": f, "address": a} for f, a in self.addresses]

    def public(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "expires_at_monotonic_ns": self.expires_at_monotonic_ns,
            "port": hp.BROKER_PORT,
            "addresses": self.rows(),
        }

    def remaining_ns(self, now_ns: int) -> int:
        return self.expires_at_monotonic_ns - now_ns


def snapshot_digest(
    origin: str,
    resolver: str,
    chain: Iterable[str],
    rows: list[dict[str, Any]],
    ttl: int,
) -> str:
    return hp.digest(
        {
            "origin": origin,
            "resolver": resolver,
            "chain": list(chain),
            "addresses": rows,
            "port": hp.BROKER_PORT,
            "ttl_s": ttl,
        }
    )


def resolve(
    origin: str,
    *,
    resolver: str,
    aliases: AliasPolicy,
    max_addresses: int,
    exchange: Exchange | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    routable: Callable[[Any], bool] = globally_routable,
    query_id: Callable[[], int] = lambda: secrets.randbelow(65536),
) -> Snapshot:
    """Capture one snapshot for an already admitted canonical ``origin``."""
    try:
        hp.origin(origin)
    except hp.ProtocolRefused:
        raise SnapshotRefused("origin.invalid") from None
    if type(max_addresses) is not int or not 1 <= max_addresses <= hp.MAX_ADDRESSES:
        raise SnapshotRefused("capacity.invalid")
    host = origin[len("https://") :]
    ask = exchange if exchange is not None else tcp_exchange(resolver)
    captured = clock_ns()
    results = []
    for rtype in (TYPE_A, TYPE_AAAA):
        qid = query_id()
        try:
            raw = ask(encode_query(qid, host, rtype))
        except SnapshotRefused:
            raise
        except Exception:
            raise SnapshotRefused("dns.failure") from None
        answer = parse_response(raw, qid, host, rtype)
        results.append(_family_result(host, answer, rtype, aliases))
    (chain4, v4, ttl4), (chain6, v6, ttl6) = results
    if not v4 and not v6:
        raise SnapshotRefused("dns.no_address")
    # A family with no data may carry no chain; any chain it does carry must
    # be the same chain, so the two families cannot point at different hosts.
    bare4, bare6 = not v4 and chain4 == [host], not v6 and chain6 == [host]
    if not (bare4 or bare6) and chain4 != chain6:
        raise SnapshotRefused("dns.ambiguous")
    chain = chain4 if v4 else chain6
    rows: list[tuple[int, str]] = []
    for family, values in ((4, v4), (6, v6)):
        for value in values:
            parsed = ipaddress.ip_address(value)
            if not routable(parsed):
                raise SnapshotRefused("dns.address_not_routable")
            rows.append((family, str(parsed)))
    if len(rows) > max_addresses:
        raise SnapshotRefused("dns.address_overflow")
    rows.sort()
    ttls = [t for t in (ttl4, ttl6) if t is not None]
    ttl = min(ttls)
    if ttl < MIN_TTL_S:
        raise SnapshotRefused("dns.short_ttl")
    lifetime_s = min(ttl, MAX_SNAPSHOT_S)
    expires = captured + lifetime_s * 1_000_000_000
    dict_rows = [{"family": f, "address": a} for f, a in rows]
    value = snapshot_digest(origin, resolver, chain, dict_rows, ttl)
    return Snapshot(
        origin=origin,
        resolver=resolver,
        chain=tuple(chain),
        addresses=tuple(rows),
        ttl_s=ttl,
        captured_at_monotonic_ns=captured,
        expires_at_monotonic_ns=expires,
        digest=value,
    )


def validate_snapshot_addresses(
    rows: Any, routable: Callable[[Any], bool] = globally_routable
) -> list[dict[str, Any]]:
    """Recheck a snapshot's addresses where they are consumed (controller)."""
    if type(rows) is not list or not 1 <= len(rows) <= hp.MAX_ADDRESSES:
        raise SnapshotRefused("snapshot.shape")
    seen: set[str] = set()
    for row in rows:
        try:
            hp.address_row(row)
        except hp.ProtocolRefused:
            raise SnapshotRefused("snapshot.shape") from None
        if row["address"] in seen or not routable(ipaddress.ip_address(row["address"])):
            raise SnapshotRefused("snapshot.address")
        seen.add(row["address"])
    if rows != sorted(rows, key=lambda r: (r["family"], r["address"])):
        raise SnapshotRefused("snapshot.order")
    return rows


__all__ = [
    "MAX_CHAIN",
    "MIN_TTL_S",
    "AliasPolicy",
    "Snapshot",
    "SnapshotRefused",
    "globally_routable",
    "resolve",
    "validate_snapshot_addresses",
]
