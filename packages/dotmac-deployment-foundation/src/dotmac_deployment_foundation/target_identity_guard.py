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
document nobody runs. :func:`scan_archive` therefore walks every archive member,
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
* **The two exact semantic loopback constants** (:data:`LOOPBACK_CONSTANTS`)
  — and only those two, never the loopback RANGE. They are permitted because
  the typed exposure policy declares loopback and needs to spell it, and
  because neither can name a REMOTE host. They may still not IDENTIFY a target
  or a vantage: a loopback bound to a target- or vantage-shaped name is refused
  under :data:`_IDENTITY_BINDING`, because "the target defaults to localhost"
  is a default, and the ruling on defaults is that there are none.
* **An owner-defined policy CIDR of more than one address** — a private-range
  network in a policy constant is topology, not identity, and `vantage.py`'s
  `PRIVATE_RANGES` is
  the definition of the private space it refuses to sit in. A `/32` is not a
  network by this rule; it is a host wearing a prefix, and is refused.
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
import tarfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import DeploymentFoundationError
from .vantage import PRIVATE_RANGES

__all__ = [
    "EMBEDDED_TARGET_DEBT",
    "ESTATE_SUFFIXES",
    "INSPECTED_SUFFIXES",
    "EmbeddedTargetError",
    "TargetIdentityFinding",
    "LOOPBACK_CONSTANTS",
    "check_debt",
    "require_no_embedded_target",
    "scan_text",
    "scan_tree",
    "scan_archive",
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
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in PRIVATE_RANGES)
_PRIVATE_POLICY_SOURCE_SUFFIXES = (
    "src/dotmac_deployment_foundation/vantage.py",
    "dotmac_deployment_foundation/vantage.py",
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


#: The only loopback spellings permitted, as EXACT strings rather than a range
#: test. `is_loopback` would admit the whole IPv4 loopback range, and "permit
#: the loopback range" is precisely the broad rule the ruling refused: these two
#: constants are what the typed exposure policy declares (`ingress.LOOPBACK`),
#: and anything else in `127/8` is an address somebody chose.
LOOPBACK_CONSTANTS: tuple[str, ...] = ("127.0.0.1", "::1")

#: A binding whose NAME says the value identifies a host to reach. A permitted
#: constant assigned to one of these is refused anyway: the ruling is that a
#: target or vantage arrives through the typed authorization, lease and
#: challenge inputs with no defaults, and `target = <a loopback constant>` is a
#: default. (Spelled without the literal: this module is subject to its own
#: scan, and the rule caught this comment when it was written out in full.)
_IDENTITY_BINDING = re.compile(
    r"(?<![A-Za-z])(target|vantage|remote_host|probe_host|inspect_host)"
    r"[A-Za-z_]*\s*(?::[^=]*)?=\s*$",
    re.IGNORECASE,
)


def _is_permitted_address(text: str) -> bool:
    """Whether ``text`` parses as an address this guard deliberately allows.

    Returns True for anything that does not parse at all: a candidate the
    pattern found but `ipaddress` rejects is not an address, so it is not this
    guard's business and is not a finding either.
    """
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True
    if address.is_unspecified:
        return True
    if text in LOOPBACK_CONSTANTS:
        return True
    return any(address in network for network in _DOCUMENTATION_NETWORKS)


def _is_network_literal(text: str, following: str, *, where: str) -> bool:
    """Whether ``text`` is an exact permitted policy/example CIDR.

    The prefix has to be RIGHT THERE in the source. Reading it from the
    surrounding line would let an unrelated `/24` three tokens away launder a
    host literal into a range.
    """
    match = re.match(r"/(\d{1,3})(?![\d])", following)
    if match is None:
        return False
    try:
        network = ipaddress.ip_network(f"{text}/{match.group(1)}", strict=True)
    except ValueError:
        return False
    if network.num_addresses == 1:
        return False
    if network in _DOCUMENTATION_NETWORKS:
        return True
    normalized_where = where.replace("\\", "/")
    owns_private_policy = any(
        normalized_where == suffix or normalized_where.endswith(f"/{suffix}")
        for suffix in _PRIVATE_POLICY_SOURCE_SUFFIXES
    )
    return owns_private_policy and network in _PRIVATE_NETWORKS


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
            if not _is_address(candidate):
                continue
            line = _line_of(match.start())
            # A permitted constant that is being used AS an identity is still a
            # finding. This is checked before the permission, not after, so the
            # loopback exemption cannot be the thing that hides it.
            before = text[: match.start()].rsplit("\n", 1)[-1]
            quote_trimmed = before.rstrip("\"'")
            identity_binding = _IDENTITY_BINDING.search(quote_trimmed)
            network_literal = _is_network_literal(
                candidate, text[match.end() :], where=where
            )
            if identity_binding:
                findings.append(
                    TargetIdentityFinding(where, line, "address-as-identity", candidate)
                )
                continue
            if _is_permitted_address(candidate):
                continue
            if network_literal:
                continue
            findings.append(TargetIdentityFinding(where, line, kind, candidate))

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


def scan_archive(archive_path: Path) -> list[TargetIdentityFinding]:
    """Scan EVERY member of a built wheel or sdist — what ships and executes.

    The whole point of the artifact path: a tree scan describes a document
    nobody runs. Members are read from the archive rather than from any
    directory, so a file the build GENERATED is inspected on the same terms as
    one a human wrote.

    NO suffix filter here, deliberately, and this is the difference between
    this scan and a scan that walks `*.py` inside a wheel and calls it done.
    Packaged templates, rendered configuration and plain package DATA ship and
    are read on the host exactly as a module is; a literal in one of them is
    not less compiled-in for having a `.json` name. In the tree an extension
    filter is a reasonable way to skip build noise. In the artifact there is no
    noise -- every member is there because something put it there.
    """
    findings: list[TargetIdentityFinding] = []
    for name, raw in _archive_members(archive_path):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Not silently clean. A member that could not be read is a member
            # nobody looked inside, and the one thing a scan may never do is
            # turn "could not look" into zero.
            findings.append(
                TargetIdentityFinding(name, 0, "unreadable", Path(name).suffix)
            )
            continue
        findings.extend(scan_text(name, text))
    return findings


def _archive_members(archive_path: Path) -> Iterator[tuple[str, bytes]]:
    """Every FILE member of a wheel (zip) or an sdist (tar.gz), as raw bytes.

    Both artifact kinds, because the publication ruling scans both and because
    an sdist is a perfectly good way to ship a literal that never appears in a
    wheel. Dispatch is on what the file IS, not on its name: a wheel is a zip
    and an sdist is a tar, and `zipfile`/`tarfile` are asked directly.
    """
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                yield info.filename, archive.read(info)
        return
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as tar:
            for member in sorted(tar.getmembers(), key=lambda item: item.name):
                if not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None:  # pragma: no cover - isfile() already ruled it out
                    continue
                with handle:
                    yield member.name, handle.read()
        return
    raise EmbeddedTargetError(
        f"{archive_path.name} is neither a wheel nor an sdist. An artifact the "
        "scanner cannot open is an artifact nobody inspected, which is refused "
        "rather than reported as clean."
    )


#: Literals already embedded when this guard was written, frozen by PATH and
#: COUNT — never by value, which would put the refused addresses into the very
#: artifact that refuses them.
#:
#: Two-directional (AGENTS.md rule 23): a rise is new debt and a fall is debt
#: paid without the ledger being lowered. Both fail, because a one-directional
#: ratchet lets a number drift down through unrelated edits until it stops
#: describing anything.
EMBEDDED_TARGET_DEBT: Mapping[str, int] = {
    # RFC 1918 examples in comments about IPv4-mapped addresses. `exposure.py`
    # is mid-refactor in the release lane and out of this seam; these move when
    # it is back in.
    "src/dotmac_deployment_foundation/exposure.py": 2,
    # An estate hostname naming the extraction SOURCE file this renderer was
    # ported from, in another repository. Provenance for a port, not a
    # connection target and not an address -- outside the estate-address ruling
    # and left for a hostname decision of its own.
    "src/dotmac_deployment_foundation/render/nginx.py": 2,
    "EXTRACTION.toml": 5,
}


#: Distribution import name, used to map an archive member back onto a ledger
#: key. Named once rather than spelled at each use.
_IMPORT_NAME = "dotmac_deployment_foundation"


def ledger_key(where: str) -> str:
    """Map a tree path OR an archive member onto one ledger key.

    Three spellings name the same file. In the tree it is
    ``src/<pkg>/lease.py``; in a wheel it is ``<pkg>/lease.py``; in an sdist it
    is ``<dist>-<version>/src/<pkg>/lease.py``. A ledger keyed by only one of
    them would silently allow the other two, which is the whole failure mode
    the artifact scan exists to close.
    """
    name = where.replace("\\", "/")
    parts = name.split("/")
    # An sdist's single root directory, `<distribution>-<version>/`.
    if len(parts) > 1 and parts[0].startswith(_IMPORT_NAME.replace("_", "-")):
        parts = parts[1:]
        name = "/".join(parts)
    # A wheel carries the import package at the archive root.
    if parts and parts[0] == _IMPORT_NAME:
        name = f"src/{name}"
    return name


def check_debt(
    findings: Iterable[TargetIdentityFinding],
    *,
    inspected: set[str] | None = None,
) -> list[str]:
    """Compare findings against the frozen ledger. Empty means unchanged.

    Returns human-readable complaints rather than raising, so a caller can
    report every drift at once instead of one per run.

    ``inspected`` narrows the FALLING direction to the ledger entries a given
    scan could actually have seen. A wheel does not carry `EXTRACTION.toml` or
    the test tree, so demanding the full ledger from a wheel would report every
    absent entry as debt paid without being lowered -- noise that trains a
    reviewer to ignore the ratchet. The RISING direction is never narrowed: a
    literal in a member with no ledger entry fails wherever it is found. The
    two-directional ratchet lives on the tree scan, which sees everything.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        counts[ledger_key(finding.where)] = counts.get(ledger_key(finding.where), 0) + 1

    considered = set(EMBEDDED_TARGET_DEBT) if inspected is None else set(inspected)
    complaints: list[str] = []
    for where in sorted(set(counts) | considered):
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
