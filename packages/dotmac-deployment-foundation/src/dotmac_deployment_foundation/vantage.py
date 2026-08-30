"""``VantageQualification.v1`` — proving a probe host may be believed.

`94.72.99.155` was qualified on 2026-08-29 as "outside every Dotmac allowlist"
on the strength of three refusals and one positive control. It then turned out
to hold a second NIC, `eth1 10.0.0.4/22`, routing into the `idp-ha` private
network. The refusals were real and the conclusion was not: a vantage untrusted
publicly and inside the perimeter privately is the more dangerous shape, because
its refusals read as proof of isolation that the private path never had to
satisfy.

Re-measured 2026-08-30, that NIC is **gone**. Which creates the problem this
module exists for.

## A removed risk that also removed the control

The old qualification's discriminating control was
``ip route get 10.0.0.2 -> dev eth1``: proof that the routing query SELECTS a
path rather than always naming the same interface. With the NIC detached, that
query answers `eth0` like everything else. The risk is resolved and **the
control is gone with it**, and those are different facts. A check that stops
discriminating because the thing it discriminated against was removed has been
lost, not passed.

So qualification is no longer "no second NIC was found". It is a set of POSITIVE
proofs, each of which fails loudly when absent:

1. exactly one non-loopback interface, and it is the declared public one;
2. no interface or route into the private range;
3. no tunnel device of any kind;
4. every former private path is unreachable, probed and recorded;
5. no fleet credential material present;
6. the route to the target leaves via the public interface, **per family**;
7. the target observes the expected public source identity — the far-end
   confirmation, which is the one proof the vantage cannot fake about itself.

Item 7 is what replaces the lost discrimination control. Everything above it is
the vantage describing itself; only item 7 is measured from the other end, and a
vantage that reports a clean interface list while egressing from somewhere else
fails there and nowhere else.

## No network I/O here

Every field is a typed OBSERVATION the runner supplies, exactly as
`ProbeResult` and `ProbeVantage` are. This facility holds zero runtime
dependencies and performs no I/O; it decides whether the measurements amount to
a qualification, which is the part that must be reviewable.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from collections.abc import Mapping
from typing import Final

from .errors import SpecError

__all__ = [
    "PRIVATE_RANGES",
    "TUNNEL_KINDS",
    "VantageQualification",
    "qualify_vantage",
]

#: Ranges a neutral external vantage must not sit in or route into. The
#: `idp-ha` network the retracted NIC reached is the concrete case.
PRIVATE_RANGES: Final[tuple[str, ...]] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)

#: Any of these on the vantage means traffic can leave by a path the interface
#: list does not describe.
TUNNEL_KINDS: Final[tuple[str, ...]] = (
    "wireguard",
    "tun",
    "tap",
    "gre",
    "vti",
    "ipip",
    "sit",
    "geneve",
    "vxlan",
)


@dataclasses.dataclass(frozen=True, slots=True)
class VantageQualification:
    """The measurements, and the verdict derived from them."""

    address_v4: str
    address_v6: str
    public_interface: str
    #: interface name -> the addresses observed on it, from `ip -br addr`.
    interfaces: Mapping[str, tuple[str, ...]]
    #: Every device kind seen in `ip -d link show`, so an empty tuple is a
    #: measured absence rather than a field nobody filled in.
    link_kinds: tuple[str, ...]
    #: target -> interface that `ip route get` selected, per family.
    routes_to_target: Mapping[str, str]
    #: Former private paths, each proved UNREACHABLE. Empty is refused: an
    #: unprobed path is not an absent one.
    private_paths_unreachable: Mapping[str, bool]
    #: Names of fleet credential markers searched for, and whether found.
    credential_markers: Mapping[str, bool]
    #: What the TARGET saw as the source address of a connection from here.
    observed_source_v4: str
    observed_source_v6: str

    @property
    def refusals(self) -> tuple[str, ...]:
        return _refusals(self)

    @property
    def qualified(self) -> bool:
        return not self.refusals


def _in_private_range(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("/")[0])
    except ValueError:
        return False
    return any(
        parsed in ipaddress.ip_network(candidate, strict=False)
        for candidate in PRIVATE_RANGES
        if parsed.version == ipaddress.ip_network(candidate, strict=False).version
    )


def _refusals(observed: VantageQualification) -> tuple[str, ...]:
    problems: list[str] = []

    # 1. exactly one non-loopback interface, and it is the declared public one.
    non_loopback = sorted(
        name for name in observed.interfaces if name not in ("lo", "lo0")
    )
    if non_loopback != [observed.public_interface]:
        problems.append(
            f"the vantage has non-loopback interfaces {non_loopback}, and a "
            f"qualified vantage has exactly one: {observed.public_interface!r}. "
            "A second interface is a second path, and the interface list is the "
            "only thing that makes 'the route left via eth0' meaningful"
        )

    # 2. nothing in, or routed into, the private ranges.
    for name, addresses in sorted(observed.interfaces.items()):
        for address in addresses:
            if _in_private_range(address):
                problems.append(
                    f"interface {name!r} holds {address}, which is inside "
                    f"{PRIVATE_RANGES}. This is the exact shape retracted on "
                    "2026-08-29: publicly untrusted and privately inside the "
                    "perimeter"
                )

    # 3. no tunnels.
    tunnels = sorted({kind for kind in observed.link_kinds if kind in TUNNEL_KINDS})
    if tunnels:
        problems.append(
            f"the vantage carries tunnel device(s) {tunnels}. Traffic can leave "
            "by a path the interface list does not describe, so no refusal "
            "measured here is scoped to a known transport"
        )

    # 4. former private paths, each PROVED unreachable.
    if not observed.private_paths_unreachable:
        problems.append(
            "no former private path was probed. An unprobed path is not an "
            "absent one, and this is the check that the retracted NIC's reach "
            "is genuinely gone rather than merely unlisted"
        )
    reachable = sorted(
        target
        for target, unreachable in observed.private_paths_unreachable.items()
        if not unreachable
    )
    if reachable:
        problems.append(
            f"former private path(s) {reachable} are still REACHABLE from this "
            "vantage; its refusals cannot be read as isolation"
        )

    # 5. no fleet credentials.
    found = sorted(
        marker for marker, present in observed.credential_markers.items() if present
    )
    if found:
        problems.append(
            f"fleet credential material {found} is present. A vantage holding "
            "fleet credentials is a fleet host, and a fleet host is not an "
            "outside vantage"
        )
    if not observed.credential_markers:
        problems.append(
            "no credential markers were searched for; absence was assumed "
            "rather than measured"
        )

    # 6. per-family route to the target leaves via the public interface.
    if not observed.routes_to_target:
        problems.append(
            "no route to the target was recorded. A vantage's own egress check "
            "does not establish the path to one specific target"
        )
    for family, interface in sorted(observed.routes_to_target.items()):
        if interface != observed.public_interface:
            problems.append(
                f"the {family} route to the target leaves via {interface!r}, not "
                f"{observed.public_interface!r}; any refusal on that family is "
                "scoped to a transport this qualification does not cover"
            )

    # 7. the far end agrees. The only proof here the vantage cannot fake.
    for family, expected, seen in (
        ("IPv4", observed.address_v4, observed.observed_source_v4),
        ("IPv6", observed.address_v6, observed.observed_source_v6),
    ):
        if not seen.strip():
            problems.append(
                f"the target never recorded the {family} source address of a "
                "connection from this vantage. This is the one check measured "
                "from the far end, and it is what replaces the discrimination "
                "control lost when the second NIC was removed"
            )
        elif seen.strip() != expected.strip():
            problems.append(
                f"the target observed {family} source {seen!r} while the vantage "
                f"reports {expected!r}. The vantage is egressing from somewhere "
                "other than it believes, which its own interface list cannot show"
            )
    return tuple(problems)


def qualify_vantage(observed: VantageQualification) -> VantageQualification:
    """Return `observed` if it qualifies, else refuse with every reason at once.

    All reasons together rather than the first: a vantage with three defects
    should not take three rounds to fix, and a reader who sees only the first
    tends to assume it is the only one.
    """
    problems = observed.refusals
    if problems:
        raise SpecError(
            "the probe vantage is not qualified, so its refusals prove nothing: "
            + "; ".join(problems)
        )
    return observed
