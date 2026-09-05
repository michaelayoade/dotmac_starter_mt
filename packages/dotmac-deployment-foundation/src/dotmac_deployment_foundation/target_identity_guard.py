"""A target host's identity arrives at RUNTIME, and is never built into the artifact.

The consumer census this facility carries is invoked against named production
hosts. Those hosts must appear in the operator's INVOCATION and nowhere else:
an address compiled into the wheel is an address that survives every
environment, ships to every consumer of the distribution, and is readable by
anyone who can `pip download` it. The same literal is also how a probe pointed
at staging reaches production after a rebuild nobody re-reviewed.

## Why this scans the BUILT ARTIFACT and not only the tree

A source-tree scan answers "is the checkout clean", which is not the question.
What executes on a host is the wheel, and the two differ: packaged templates,
generated modules, data files and anything a build step writes are present in
one and absent from the other. A guard that reads only `src/` reports on a
document nobody runs. :func:`scan_wheel` therefore walks the archive members,
and :func:`scan_tree` walks the checkout, and both exist because neither
contains the other.

It is also RECURSIVE, deliberately. The nearest comparable guard in the fleet
(`dotmac_observability`'s `test_no_source_file_hardcodes_a_host`) iterates
`SRC.glob("*.py")` — top level, source only, Python only — so a literal one
directory down, or in a packaged template, or in the wheel, is invisible to it.
That shape is not reproduced here.

## What is refused

1. An IPv4 or IPv6 address literal, in any inspected file.
2. A hostname under a declared estate suffix (:data:`ESTATE_SUFFIXES`).

## What is permitted, each on a premise that is ENFORCED rather than asserted

A guard that refused every address would be trivially green against a planted
literal AND would refuse the wildcard bind that legitimate rendering needs — it
would pass its own sensitivity test while being unusable. So the permissions are
narrow, and each is decided by :mod:`ipaddress` rather than by a spelling:

* **Unspecified** (`0.0.0.0`, `::`) — not a host at all. It is "bind on every
  interface", and which interface the outside world reaches is decided by an
  overridable port knob, not by this literal.
* **Loopback** (`127.0.0.0/8`, `::1`) — structurally incapable of naming a
  REMOTE target. Whatever else a loopback literal is, it cannot be the host an
  authorized inspection was pointed at, which is the only thing this guard is
  about.
* **IANA documentation ranges** (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`, `2001:db8::/32`) — these exist precisely so that an example
  cannot name a real host. An example needing an address has a correct one to
  reach for.

Note what is NOT permitted: RFC 1918 private space. A `10.x` is a reachable
estate target, not a safe example: `vantage.py` records a probe host whose
second NIC routed into a private network, which is exactly the reachability
this guard exists to keep out of a shipped artifact. (That address is not
repeated here — this module is subject to its own scan.)

## The debt ratchet

:data:`EMBEDDED_TARGET_DEBT` freezes the literals already in this package when
the guard was written. It is TWO-DIRECTIONAL (AGENTS.md rule 23): a count that
rises fails, and a count that falls fails until the entry is lowered, so debt
cannot be paid down silently and cannot grow under cover of an existing entry.
It is keyed by PATH and COUNT and never by value — writing the offending
addresses in here would embed in this artifact the very literals it refuses.

The two entries that matter are `lease.py` and `vantage.py`: both carry a
GLOBALLY ROUTABLE address in a module docstring, recording where a measurement
was taken. Provenance is a real need and the address is real evidence, so the
resolution is a decision for the owner of those modules, not a deletion this
guard should make on its way past.
"""

from __future__ import annotations

import ipaddress
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import DeploymentFoundationError

__all__ = [
    "EMBEDDED_TARGET_DEBT",
    "ESTATE_SUFFIXES",
    "INSPECTED_SUFFIXES",
    "EmbeddedTargetError",
    "TargetIdentityFinding",
    "check_debt",
    "require_no_embedded_target",
    "scan_text",
    "scan_tree",
    "scan_wheel",
]


class EmbeddedTargetError(DeploymentFoundationError):
    """A target identity was found built into the artifact or the tree."""


@dataclass(frozen=True)
class TargetIdentityFinding:
    """One refused literal. Carries WHERE and WHAT KIND, never a wider excerpt.

    The value itself is reported because an operator cannot fix a literal they
    cannot find, and it is already in the file being reported on — unlike a
    secret, an address here is not made more exposed by naming it in the
    failure. What is deliberately absent is surrounding line text, which is how
    an adjacent credential would ride along into a CI log.
    """

    where: str
    line: int
    kind: str
    value: str

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: {self.kind} {self.value!r}"


#: Domain suffixes belonging to the estate. A hostname under one of these is a
#: real Dotmac host and never a neutral example. Deliberately a CLOSED list: an
#: "any FQDN" rule would refuse `pypi.org`, `ghcr.io` and every schema URL, and
#: a guard that cries wolf gets an exemption written for it within a week.
#:
#: This module is the one place these names may be spelled, on the same premise
#: `dotmac_observability` grants its own detector: a matcher forbidden to write
#: the shape it matches matches nothing. `test_the_estate_suffix_exemption_is_
#: used` enforces that premise rather than trusting it.
ESTATE_SUFFIXES: tuple[str, ...] = ("dotmac.io",)

#: The one file permitted to SPELL an estate suffix, because it is the file that
#: matches on it — a detector forbidden to write the shape it looks for detects
#: nothing. Exactly the exemption `dotmac_observability` grants its own
#: `validate.py`, and held to the same standard: the premise is ENFORCED by
#: `test_the_estate_suffix_exemption_states_an_enforceable_premise`, which fails
#: if the name ever stops living inside `ESTATE_SUFFIXES`. The exemption covers
#: the hostname rule ONLY; this module is scanned for addresses like any other,
#: and that scan already caught a literal in its own docstring.
_DETECTOR_MODULE = "target_identity_guard.py"

#: File kinds inspected: production Python, packaged scripts and templates, and
#: operative configuration. Extensions rather than a curated file list, because
#: a list is a thing somebody forgets to append to.
INSPECTED_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".sh",
    ".bash",
    ".j2",
    ".jinja",
    ".jinja2",
    ".tmpl",
    ".template",
    ".conf",
    ".cfg",
    ".ini",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".env",
    ".service",
    ".timer",
)

# Ranges that exist so an example cannot name a real host (RFC 5737, RFC 3849).
_DOCUMENTATION_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)

# A dotted quad, bounded so it does not bite out of the middle of a longer
# dotted string. Validity is decided by `ipaddress`, not by this pattern: the
# pattern's job is to find candidates, and octet range is not its business.
_V4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")

# IPv6, on the SAME principle as the quad above and for a sharper reason: a
# hand-written IPv6 grammar is where this guard first went wrong. The first cut
# spelled out `(?:[0-9a-fA-F]{1,4}:){2,7}...` and silently missed BOTH
# a unique-local address (one hex group before the elision, so `{2,7}` never
# matched)
# and a full global address ending in a hex group (the trailing lookahead
# rejected its own match). Two misses, in the family an estate reachable over
# v6 would actually use, and both read as clean.
#
# So the pattern's only job is to find a RUN of hex and colons carrying at
# least two colons, and `ipaddress` decides whether it is an address. A Python
# slice, a `sha256:` prefix and a `12:30:45` timestamp all reach the parser and
# are all rejected by it -- the correct division of labour, since the parser
# already knows the grammar and the regex never will.
_V6 = re.compile(
    r"(?<![\w:.])(?=[0-9a-fA-F:]*:[0-9a-fA-F:]*:)[0-9a-fA-F:]{2,45}(?![\w:.])"
)

_HOSTNAME = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_-]+\.)+[A-Za-z]{2,}(?![\w-])")


def _is_permitted_address(text: str) -> bool:
    """Whether ``text`` parses as an address this guard deliberately allows.

    Returns False for anything that does not parse at all: a candidate the
    pattern found but `ipaddress` rejects is not an address, so it is not this
    guard's business and is not a finding either.
    """
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True
    if address.is_unspecified or address.is_loopback:
        return True
    return any(address in network for network in _DOCUMENTATION_NETWORKS)


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def scan_text(where: str, text: str) -> list[TargetIdentityFinding]:
    """Every refused target identity in ``text``, in file order."""
    findings: list[TargetIdentityFinding] = []

    def _line_of(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    for pattern, kind in ((_V4, "ipv4-literal"), (_V6, "ipv6-literal")):
        for match in pattern.finditer(text):
            candidate = match.group(0)
            if not _is_address(candidate) or _is_permitted_address(candidate):
                continue
            findings.append(
                TargetIdentityFinding(where, _line_of(match.start()), kind, candidate)
            )

    detector = where.replace("\\", "/").endswith(_DETECTOR_MODULE)
    for match in _HOSTNAME.finditer(text):
        if detector:
            break
        host = match.group(0).lower().rstrip(".")
        # Dot-BOUNDED containment, not `endswith`. `selfcare.dotmac.io.conf` is
        # an estate host wearing a file extension, and `endswith` walked past
        # it -- the guard's own ledger is what caught that.
        padded = f".{host}."
        if not any(f".{suffix}." in padded for suffix in ESTATE_SUFFIXES):
            continue
        findings.append(
            TargetIdentityFinding(
                where, _line_of(match.start()), "estate-hostname", host
            )
        )

    return sorted(
        findings, key=lambda finding: (finding.line, finding.kind, finding.value)
    )


def _inspectable(name: str) -> bool:
    return name.endswith(INSPECTED_SUFFIXES)


def scan_tree(root: Path) -> list[TargetIdentityFinding]:
    """Recursively scan a checkout. Paths are reported relative to ``root``.

    `rglob`, not `glob`: a literal one directory down is the ordinary case, not
    the exotic one, and the guard this replaces could not see it.
    """
    findings: list[TargetIdentityFinding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if not _inspectable(path.name):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable package data. Not silently clean: it is
            # reported as an unreadable member so the count cannot be a lie.
            findings.append(
                TargetIdentityFinding(
                    str(path.relative_to(root)), 0, "unreadable", path.suffix
                )
            )
            continue
        findings.extend(scan_text(str(path.relative_to(root)), text))
    return findings


def scan_wheel(wheel: Path) -> list[TargetIdentityFinding]:
    """Scan the members of a built wheel — what actually ships and executes.

    The whole point of the artifact path: a tree scan describes a document
    nobody runs. Members are read from the archive rather than from any
    directory, so a file the build GENERATED is inspected on the same terms as
    one a human wrote.
    """
    findings: list[TargetIdentityFinding] = []
    with zipfile.ZipFile(wheel) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or not _inspectable(name):
                continue
            raw = archive.read(name)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                findings.append(
                    TargetIdentityFinding(name, 0, "unreadable", Path(name).suffix)
                )
                continue
            findings.extend(scan_text(name, text))
    return findings


#: Literals already embedded when this guard was written, frozen by PATH and
#: COUNT — never by value, which would put the refused addresses into the very
#: artifact that refuses them.
#:
#: Two-directional (AGENTS.md rule 23): a rise is new debt and a fall is debt
#: paid without the ledger being lowered. Both fail, because a one-directional
#: ratchet lets a number drift down through unrelated edits until it stops
#: describing anything.
EMBEDDED_TARGET_DEBT: Mapping[str, int] = {
    # GLOBALLY ROUTABLE addresses in module docstrings, recording the host a
    # measurement was taken on. These are the two that matter: the address IS
    # the evidence, so removing it costs something real, and that trade-off
    # belongs to these modules' owner rather than to the guard walking past.
    "src/dotmac_deployment_foundation/lease.py": 1,
    "src/dotmac_deployment_foundation/vantage.py": 6,
    # RFC 1918 examples in prose. Each has a correct replacement available in
    # the documentation ranges, so this debt is cheap to pay.
    "src/dotmac_deployment_foundation/ingress.py": 2,
    "src/dotmac_deployment_foundation/exposure.py": 2,
    # An estate hostname naming the extraction SOURCE file this renderer was
    # ported from -- provenance, not a connection target.
    "src/dotmac_deployment_foundation/render/nginx.py": 2,
    "EXTRACTION.toml": 5,
}


def check_debt(findings: Iterable[TargetIdentityFinding]) -> list[str]:
    """Compare findings against the frozen ledger. Empty means unchanged.

    Returns human-readable complaints rather than raising, so a caller can
    report every drift at once instead of one per run.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.where] = counts.get(finding.where, 0) + 1

    complaints: list[str] = []
    for where in sorted(set(counts) | set(EMBEDDED_TARGET_DEBT)):
        found = counts.get(where, 0)
        allowed = EMBEDDED_TARGET_DEBT.get(where, 0)
        if found > allowed:
            complaints.append(
                f"{where}: {found} embedded target identities, "
                f"ledger allows {allowed}. A target host reaches this "
                "facility through the operator's invocation, never "
                "through a literal in the artifact."
            )
        elif found < allowed:
            complaints.append(
                f"{where}: {found} embedded target identities, "
                f"ledger still claims {allowed}. Lower the entry in "
                "EMBEDDED_TARGET_DEBT in the same change that removed the "
                "literal, or the ledger stops describing the tree."
            )
    return complaints


def require_no_embedded_target(findings: Iterable[TargetIdentityFinding]) -> None:
    """Raise :class:`EmbeddedTargetError` unless ``findings`` match the ledger."""
    complaints = check_debt(findings)
    if not complaints:
        return
    raise EmbeddedTargetError("\n  - ".join(["embedded target identity:", *complaints]))


def _iter_candidates(text: str) -> Iterator[str]:
    """Every address-shaped candidate, permitted or not. For tests and tooling."""
    for pattern in (_V4, _V6):
        for match in pattern.finditer(text):
            yield match.group(0)
