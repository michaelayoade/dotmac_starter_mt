"""Execution and proof for `IngressPolicy.v1`: apply it, then go and look.

Slice 1 decides what the exposure SHOULD be and renders it. This module is the
other half: apply the rendered plan transactionally, then re-observe the host
and compare what is actually listening against what was declared. A renderer
that is never checked against a socket is a renderer that is right until it
isn't.

## Why re-observation is not optional here

Every one of the following was measured on this fleet, and each one defeats a
check that stops at "the file says loopback":

**`docker compose restart` does not re-render ports.** It restarts the
container it already has, with the port bindings it already has. Only `up -d`
(with `--force-recreate` when the image is unchanged) creates a new container
from the current file. A correct Compose diff followed by a `restart` leaves
the OLD binding live and the NEW one reviewed, merged and believed — so
:func:`refuse_non_recreating_apply` refuses an apply whose command cannot
change a binding.

**Hosts differ in closed-port behaviour.** One host in this fleet silently
DROPs a connection to a closed port; another RSTs. On the dropping host an
external probe CANNOT distinguish "bound to loopback" from "bound to the
wildcard and firewalled", because both look like silence. So a probe alone
never concludes a binding here: :func:`conclude_binding` requires on-host
socket evidence and returns `INCONCLUSIVE` rather than guessing.

**A probe from inside an allowlist proves nothing.** This is the expensive one.
The workstation's public address sits inside a range several of this fleet's
allowlists explicitly ACCEPT. Two independent agents each connected to a
"public" port from it, and each escalated a P0 that did not exist. A successful
connection is evidence of reachability FROM THAT VANTAGE, and nothing more.
:func:`accept_public_exposure_evidence` refuses a probe whose vantage is inside
— or is not KNOWN to be outside — every source set the plan would accept.

**A chain can be inert.** An `ip6tables DOCKER-USER` rule for a published port
never fires, so a verifier that counts rules rather than checking the chain
they live in reports containment that does not exist (see :mod:`.ingress`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from . import ingress
from .engine.lock import DEFAULT_LOCK_DIR, deployment_lock
from .errors import DeploymentError, PreconditionFailed, SpecError
from .policy import build_firewall_plan
from .spec import ProductDeploymentSpec

__all__ = [
    "APPLY_COMMAND",
    "Binding",
    "ExposureEffects",
    "ExposureTransaction",
    "Finding",
    "HostObservation",
    "ObservedChain",
    "ObservedProxy",
    "ObservedRule",
    "ObservedSocket",
    "PrivilegedVantageError",
    "ProbeOutcome",
    "ProbeResult",
    "ProbeVantage",
    "Severity",
    "VerificationReport",
    "accept_public_exposure_evidence",
    "apply_exposure",
    "conclude_binding",
    "expected_bindings",
    "observation_from_text",
    "parse_docker_proxy_processes",
    "parse_iptables_save",
    "parse_socket_listing",
    "refuse_non_recreating_apply",
    "verify_exposure",
]


class PrivilegedVantageError(DeploymentError):
    """A probe was offered as public-exposure evidence from a privileged place.

    Its own error class because the refusal is a CONCLUSION about the evidence,
    not a failure of the probe: the connection really did succeed, and it still
    says nothing about the public internet.
    """


#: The only apply shape that can change a port binding. `restart` reuses the
#: existing container and therefore the existing bindings; `up -d` alone will
#: not recreate when the image is unchanged, which is exactly the case a
#: bind-only change is.
APPLY_COMMAND: Final[tuple[str, ...]] = ("up", "-d", "--force-recreate")

_LOOPBACK = frozenset(ingress.LOOPBACK.values())


class Severity(str, Enum):
    """Two levels, deliberately.

    `REFUSE` rolls the transaction back. `NOTE` is recorded and does not.
    A third, advisory level would immediately collect everything nobody wanted
    to act on, which is how a report stops being read.
    """

    REFUSE = "refuse"
    NOTE = "note"


class ProbeOutcome(str, Enum):
    REACHED = "reached"
    REFUSED = "refused"
    SILENT = "silent"


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    code: str
    detail: str


# ── observations ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ObservedSocket:
    """One listening socket, as `ss -tlnp` reported it."""

    family: str
    address: str
    port: int
    process: str


@dataclass(frozen=True, slots=True)
class ObservedProxy:
    """One `docker-proxy` process, with the binding it was started for.

    Observed SEPARATELY from the socket, and compared against it, because they
    can disagree: a leftover proxy from a container that was replaced holds a
    binding the current Compose file does not describe.
    """

    family: str
    host_ip: str
    host_port: int
    container_port: int
    protocol: str


@dataclass(frozen=True, slots=True)
class ObservedRule:
    family: str
    chain: str
    arguments: str

    @property
    def target(self) -> str:
        match = re.search(r"-j\s+(\S+)", self.arguments)
        return match.group(1) if match else ""

    @property
    def matched_port(self) -> int | None:
        match = re.search(r"--(?:ctorigdstport|dport)\s+(\d+)", self.arguments)
        return int(match.group(1)) if match else None

    @property
    def matches_original_destination(self) -> bool:
        return "--ctorigdstport" in self.arguments


@dataclass(frozen=True, slots=True)
class ObservedChain:
    family: str
    name: str
    policy: str
    rules: tuple[ObservedRule, ...]

    def rules_for(self, host_port: int) -> tuple[ObservedRule, ...]:
        return tuple(rule for rule in self.rules if rule.matched_port == host_port)


@dataclass(frozen=True, slots=True)
class HostObservation:
    """Everything the verifier is allowed to reason from.

    A frozen value rather than a live handle, so a snapshot taken before an
    apply and the re-observation taken after are the SAME type and can be
    diffed, and so a verification is reproducible from a recorded observation
    months later.
    """

    sockets: tuple[ObservedSocket, ...] = ()
    proxies: tuple[ObservedProxy, ...] = ()
    chains: tuple[ObservedChain, ...] = ()
    #: Whether this host RSTs or silently DROPs a connection to a closed port.
    #: Declared rather than inferred, and load-bearing: see
    #: :func:`conclude_binding`.
    closed_port_behaviour: str = "unknown"

    def chain(self, family: str, name: str) -> ObservedChain | None:
        for candidate in self.chains:
            if candidate.family == family and candidate.name == name:
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class Binding:
    """One (family, port) the policy expects, and what it expects there."""

    endpoint_token: str
    service: str
    family: str
    host_port: int
    container_port: int
    protocol: str
    exposure: str
    source_set: str


# ── parsers (pure, so a recorded observation can be replayed) ───────────────

_SS_LINE = re.compile(
    r"^LISTEN\s+\d+\s+\d+\s+(?P<local>\S+)\s+\S+(?:\s+(?P<rest>.*))?$"
)
_SS_PROCESS = re.compile(r'\("(?P<name>[^"]+)"')
_PROXY = re.compile(
    r"docker-proxy.*?-proto\s+(?P<proto>\w+)\s+-host-ip\s+(?P<ip>\S+)\s+"
    r"-host-port\s+(?P<hport>\d+)\s+.*?-container-port\s+(?P<cport>\d+)"
)


def _split_host_port(local: str) -> tuple[str, int] | None:
    """`127.0.0.1:8003` and `[::1]:8003` and `*:8003`, one answer.

    IPv6 is why this is a function: splitting on the last colon is right and
    splitting on the first colon silently reads `::1` as host `` port `:1`.
    """
    if ":" not in local:
        return None
    address, _, port_text = local.rpartition(":")
    if not port_text.isdigit():
        return None
    address = address.strip("[]")
    return address, int(port_text)


def parse_socket_listing(text: str) -> tuple[ObservedSocket, ...]:
    """`ss -tlnp` output into typed sockets.

    On-host evidence, and the ONLY evidence that can conclude a binding — see
    :func:`conclude_binding` for why an external probe cannot.
    """
    sockets: list[ObservedSocket] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("LISTEN"):
            continue
        match = _SS_LINE.match(stripped)
        if match is None:
            continue
        split = _split_host_port(match.group("local"))
        if split is None:
            continue
        address, port = split
        process_match = _SS_PROCESS.search(match.group("rest") or "")
        # `*` is how `ss` renders the wildcard for both families. Normalized to
        # the literal so a downstream check compares one spelling.
        if address == "*":
            address = "::"
        # Family from the PARSED address rather than from "does it contain a
        # colon": the crude test is right for every address `ss` actually
        # prints and wrong for the one that matters, `::ffff:10.0.0.1`, which
        # contains dots and colons both.
        try:
            family = ingress.normalize_address(address, where="<ss>").family
        except SpecError:
            # Kept, not dropped. A socket whose address this cannot classify —
            # an IPv4-mapped `::ffff:10.0.0.1`, say — is the most interesting
            # one on the host, and skipping it would remove it from the
            # undeclared-socket sweep as well. `unknown` matches no declared
            # family, so it surfaces as a refusal instead of as silence.
            family = "unknown"
        sockets.append(
            ObservedSocket(
                family=family,
                address=address,
                port=port,
                process=process_match.group("name") if process_match else "",
            )
        )
    return tuple(sockets)


def parse_docker_proxy_processes(text: str) -> tuple[ObservedProxy, ...]:
    """`ps` output into the bindings `docker-proxy` was actually started for."""
    proxies: list[ObservedProxy] = []
    for line in text.splitlines():
        match = _PROXY.search(line)
        if match is None:
            continue
        host_ip = match.group("ip").strip("[]")
        proxies.append(
            ObservedProxy(
                family="ipv6" if ":" in host_ip else "ipv4",
                host_ip=host_ip,
                host_port=int(match.group("hport")),
                container_port=int(match.group("cport")),
                protocol=match.group("proto"),
            )
        )
    return tuple(proxies)


def parse_iptables_save(text: str, *, family: str) -> tuple[ObservedChain, ...]:
    """`iptables-save`/`ip6tables-save` output into chains and their rules.

    Chains are kept even when empty, because "the chain exists and holds
    nothing" and "the chain does not exist" are different facts and only the
    first one is a missing rule.
    """
    if family not in ingress.FAMILIES:
        raise SpecError(f"unknown address family {family!r}")
    policies: dict[str, str] = {}
    rules: dict[str, list[ObservedRule]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(":"):
            parts = line[1:].split()
            if parts:
                policies[parts[0]] = parts[1] if len(parts) > 1 else "-"
                rules.setdefault(parts[0], [])
        elif line.startswith("-A "):
            remainder = line[3:].strip()
            name, _, arguments = remainder.partition(" ")
            rules.setdefault(name, []).append(
                ObservedRule(family=family, chain=name, arguments=arguments.strip())
            )
            policies.setdefault(name, "-")
    return tuple(
        ObservedChain(
            family=family,
            name=name,
            policy=policies.get(name, "-"),
            rules=tuple(rules.get(name, ())),
        )
        for name in sorted(rules)
    )


# ── what a probe may and may not conclude ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProbeVantage:
    """Where a probe was run from, and what that place is INSIDE.

    ``inside_source_sets`` is required and may be empty; it may NOT be unknown.
    A vantage that cannot say which allowlists contain it cannot be used to
    conclude anything about public reachability, and defaulting it to "outside
    everything" is precisely the assumption that cost this programme two false
    P0 escalations.
    """

    name: str
    inside_source_sets: frozenset[str]
    #: `True` only when someone has established the vantage sits outside every
    #: source set that could apply. Separate from the set above because "I
    #: checked and it is in none of them" and "I did not check" are different,
    #: and only the first may conclude.
    membership_established: bool


@dataclass(frozen=True, slots=True)
class ProbeResult:
    endpoint_token: str
    family: str
    vantage: ProbeVantage
    outcome: ProbeOutcome


def accept_public_exposure_evidence(
    probe: ProbeResult, *, accepted_source_sets: Sequence[str]
) -> None:
    """Raise unless ``probe`` may be read as evidence of PUBLIC reachability.

    Refused in two cases, and both were real:

    - the vantage is inside a source set the plan ACCEPTs, so a successful
      connection proves the allowlist works rather than that the port is
      public. The workstation sits inside a range this fleet allowlists, and
      two agents independently escalated a P0 from exactly that connection;
    - the vantage's membership was never established. Fail closed: an unproven
      vantage is not a neutral one, and treating it as neutral is the same
      error with the checking step removed.
    """
    if not probe.vantage.membership_established:
        raise PrivilegedVantageError(
            f"probe vantage {probe.vantage.name!r} has not established which "
            "source sets contain it, so a successful connection from it cannot "
            "distinguish 'publicly reachable' from 'reachable because we are on "
            "the allowlist'. Establish the vantage before concluding, or "
            "conclude nothing"
        )
    overlap = sorted(probe.vantage.inside_source_sets & set(accepted_source_sets))
    if overlap:
        raise PrivilegedVantageError(
            f"probe vantage {probe.vantage.name!r} is INSIDE {overlap}, which "
            f"the plan for {probe.endpoint_token} accepts. A connection from a "
            "privileged vantage is evidence the allowlist works, not evidence "
            "the port is public. This exact inference produced two false P0 "
            "escalations on 2026-08-29"
        )


def conclude_binding(
    *,
    probe: ProbeResult | None,
    sockets: Sequence[ObservedSocket],
    host_port: int,
    family: str,
    closed_port_behaviour: str,
) -> str:
    """What the evidence actually supports: ``"bound"``, ``"absent"``, or
    ``"inconclusive"``.

    On-host socket evidence decides. A probe never does — and on a host that
    silently DROPs a closed port it CANNOT, because loopback-bound and
    wildcard-bound-and-dropped look identical from outside. That is not a
    theoretical distinction: one host in this fleet drops and another resets,
    so the same probe means two different things depending on where it points.
    """
    if not sockets:
        if probe is not None and probe.outcome is ProbeOutcome.REACHED:
            # Reached, with no socket observed: the observation is incomplete,
            # not the port absent. Saying "absent" here would be the reassuring
            # answer and the wrong one.
            return "inconclusive"
        if closed_port_behaviour == "reset":
            return "absent"
        return "inconclusive"
    for socket in sockets:
        if socket.port == host_port and socket.family == family:
            return "bound"
    return "absent"


# ── verification ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerificationReport:
    #: The canonical DESCRIPTOR digest this verification was taken against, so
    #: a recorded report can be matched to the exact plan it proves rather than
    #: to whatever the descriptor says today.
    descriptor_digest: str
    findings: tuple[Finding, ...] = ()
    verified: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(finding.severity is Severity.REFUSE for finding in self.findings)

    @property
    def refusals(self) -> tuple[Finding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity is Severity.REFUSE
        )


def expected_bindings(spec: ProductDeploymentSpec) -> tuple[Binding, ...]:
    """Every (family, port) the descriptor says should exist on the host."""
    return tuple(
        Binding(
            endpoint_token=ingress.endpoint_token(
                product=spec.product,
                environment=spec.environment,
                role=code,
                protocol=publication.protocol,
                family=family,
                host_port=publication.host,
                exposure=publication.exposure,
            ),
            service=code,
            family=family,
            host_port=publication.host,
            container_port=publication.container,
            protocol=publication.protocol,
            exposure=publication.exposure,
            source_set=publication.source_set,
        )
        for code, publication in spec.publications
        for family in publication.families
    )


def _verify_socket(binding: Binding, observation: HostObservation) -> list[Finding]:
    findings: list[Finding] = []
    matching = [
        socket
        for socket in observation.sockets
        if socket.port == binding.host_port and socket.family == binding.family
    ]
    if not matching:
        findings.append(
            Finding(
                Severity.REFUSE,
                "socket_missing",
                f"{binding.endpoint_token}: nothing is listening on "
                f"{binding.family} port {binding.host_port}. The declared "
                "publication did not reach the host — and `docker compose "
                "restart` does not re-render ports, so a correct file plus a "
                "restart looks exactly like this",
            )
        )
        return findings
    for socket in matching:
        admitted = ingress.normalize_address(
            socket.address, where=binding.endpoint_token
        )
        if admitted.is_wildcard:
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "wildcard_bind",
                    f"{binding.endpoint_token}: listening on the wildcard. The "
                    "descriptor declared "
                    f"{binding.exposure!r}, and a wildcard is reachable on every "
                    "interface the host has and every one it grows later",
                )
            )
        elif binding.exposure == "loopback" and admitted.address not in _LOOPBACK:
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "not_loopback",
                    f"{binding.endpoint_token}: declared loopback and is "
                    f"listening on {admitted.address}",
                )
            )
        elif binding.exposure in ("private", "public") and admitted.is_loopback:
            # The failure direction nobody guards, because it looks like the
            # safe one. A private publication that resolved to loopback is
            # unreachable by the source set it exists for: an outage wearing a
            # hardening's clothes, and one nobody investigates for hours
            # because the socket is "correctly" bound.
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "narrower_than_declared",
                    f"{binding.endpoint_token}: declared {binding.exposure!r} "
                    f"for source set {binding.source_set!r} and is listening on "
                    f"{admitted.address}. Nothing outside this host can reach "
                    "it, so this is an outage rather than a hardening",
                )
            )
    # There is deliberately no "declared ipv4, listening on ipv6" check HERE.
    # It could never fire: `matching` already filters by family, so a socket on
    # the other family is invisible to this function. The defect is real and is
    # caught from the OTHER direction — a family that was declared and is not
    # bound is `socket_missing`, and a family that is bound and was not
    # declared is `undeclared_socket` in `verify_exposure`. A branch that
    # cannot fire is not a second line of defence; it is a line in a coverage
    # report (ADR-0018).
    return findings


def _verify_proxy(binding: Binding, observation: HostObservation) -> list[Finding]:
    """`docker-proxy` is checked SEPARATELY from the socket.

    A leftover proxy from a replaced container holds a binding the current
    Compose file does not describe, and it answers connections. Comparing only
    the socket listing to the file would miss it, because the socket is real
    and the file is right — they are just about different containers.
    """
    findings: list[Finding] = []
    for proxy in observation.proxies:
        if proxy.host_port != binding.host_port or proxy.family != binding.family:
            continue
        admitted = ingress.normalize_address(
            proxy.host_ip, where=binding.endpoint_token
        )
        if admitted.is_wildcard:
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "proxy_wildcard",
                    f"{binding.endpoint_token}: a docker-proxy is running with "
                    "-host-ip on the wildcard. This is the process that "
                    "TERMINATES an IPv6 connection on INPUT, which is why no "
                    "DOCKER-USER rule can contain it",
                )
            )
        if proxy.container_port != binding.container_port:
            findings.append(
                Finding(
                    Severity.NOTE,
                    "proxy_remapped",
                    f"{binding.endpoint_token}: docker-proxy forwards "
                    f"{proxy.host_port} to container port "
                    f"{proxy.container_port}, and the descriptor says "
                    f"{binding.container_port}",
                )
            )
    return findings


def _verify_firewall(binding: Binding, observation: HostObservation) -> list[Finding]:
    findings: list[Finding] = []
    if binding.exposure in ("none", "loopback"):
        return findings
    chain_name = ingress.FILTER_CHAIN[binding.family]

    inert = observation.chain(binding.family, ingress.DOCKER_USER_CHAIN)
    if (
        binding.family == "ipv6"
        and inert is not None
        and inert.rules_for(binding.host_port)
    ):
        findings.append(
            Finding(
                Severity.REFUSE,
                "inert_chain",
                f"{binding.endpoint_token}: rule(s) for this port exist in "
                "ip6tables DOCKER-USER, which is INERT for a published port — "
                "that chain is jumped only from FORWARD while an IPv6 publish "
                "terminates on INPUT inside docker-proxy. Two such production "
                "rules were found with zero packet counters while the ports "
                "were open. They read as containment and are not",
            )
        )

    chain = observation.chain(binding.family, chain_name)
    if chain is None:
        findings.append(
            Finding(
                Severity.REFUSE,
                "chain_missing",
                f"{binding.endpoint_token}: chain {chain_name} does not exist "
                f"on {binding.family}, so the derived rules are nowhere",
            )
        )
        return findings

    rules = chain.rules_for(binding.host_port)
    if not rules:
        findings.append(
            Finding(
                Severity.REFUSE,
                "rules_missing",
                f"{binding.endpoint_token}: no rule in {chain_name} matches "
                f"port {binding.host_port}",
            )
        )
        return findings

    if not any(rule.target in ("DROP", "REJECT") for rule in rules):
        findings.append(
            Finding(
                Severity.REFUSE,
                "no_terminal_deny",
                f"{binding.endpoint_token}: {chain_name} has ACCEPT rule(s) for "
                "this port and no terminal DROP. Everything the accepts miss "
                "falls through to the chain policy, which on a Docker host is "
                "ACCEPT — so the allowlist enforces nothing. Check for the "
                "deny, not only the accepts",
            )
        )

    wants_original_destination = chain_name == ingress.DOCKER_USER_CHAIN
    remapped = binding.host_port != binding.container_port
    for rule in rules:
        if (
            wants_original_destination
            and remapped
            and not rule.matches_original_destination
        ):
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "wrong_port_match",
                    f"{binding.endpoint_token}: a DOCKER-USER rule matches "
                    f"--dport {binding.host_port} on a REMAPPED publish. Post-"
                    "DNAT the destination port is the container port "
                    f"({binding.container_port}), so this rule matches nothing "
                    "at all while reading, in a diff, exactly like one that "
                    "works",
                )
            )
    return findings


def verify_exposure(
    spec: ProductDeploymentSpec, observation: HostObservation
) -> VerificationReport:
    """Compare the declared exposure with what the host is actually doing."""
    findings: list[Finding] = []
    verified: list[str] = []
    bindings = expected_bindings(spec)
    for binding in bindings:
        before = len(findings)
        findings.extend(_verify_socket(binding, observation))
        findings.extend(_verify_proxy(binding, observation))
        findings.extend(_verify_firewall(binding, observation))
        if len(findings) == before:
            verified.append(binding.endpoint_token)

    # A socket nobody declared. Checked in the OTHER direction on purpose: a
    # verifier that only walks the descriptor cannot see the port the
    # descriptor does not mention, and that is the port that gets left open.
    declared = {(binding.family, binding.host_port) for binding in bindings}
    declared |= {
        (family, route.port)
        for route in (spec.ingress.routes if spec.ingress is not None else ())
        for family in (
            (spec.ingress.upstream_address_family,) if spec.ingress is not None else ()
        )
    }
    for socket in observation.sockets:
        if socket.process != "docker-proxy":
            continue
        if (socket.family, socket.port) not in declared:
            findings.append(
                Finding(
                    Severity.REFUSE,
                    "undeclared_socket",
                    f"docker-proxy is listening on {socket.family} "
                    f"{socket.address}:{socket.port}, which this descriptor "
                    "does not declare. A leftover proxy from a replaced "
                    "container answers connections that no reviewed file "
                    "describes",
                )
            )
    return VerificationReport(
        descriptor_digest=spec.to_canonical_document().sha256_digest(),
        findings=tuple(findings),
        verified=tuple(verified),
    )


# ── transactional application ───────────────────────────────────────────────


def refuse_non_recreating_apply(command: Sequence[str]) -> None:
    """Refuse an apply that cannot change a port binding.

    `docker compose restart` restarts the container it already has, with the
    bindings it already has. A plain `up -d` will not recreate when the image
    is unchanged — and a bind-only change is exactly that case. So the apply
    must force recreation, and an apply that does not is refused before it can
    report success over an unchanged binding.
    """
    tokens = list(command)
    if "restart" in tokens:
        raise PreconditionFailed(
            "`docker compose restart` does not re-render ports: it restarts the "
            "container it already has, with the bindings it already has. A "
            "correct Compose diff followed by a restart leaves the OLD binding "
            f"live and the NEW one believed. Use {list(APPLY_COMMAND)}"
        )
    if not all(part in tokens for part in APPLY_COMMAND):
        raise PreconditionFailed(
            f"an exposure apply must be {list(APPLY_COMMAND)} — a plain `up -d` "
            "does not recreate a container whose image is unchanged, and a "
            "bind-only change is exactly that case"
        )


class ExposureEffects(Protocol):
    """Everything the transaction can do to a host.

    Narrow on purpose, and separate from the deployment `Effects`: an exposure
    change is observable and reversible in a way a migration is not, so it gets
    its own seam and its own fake. Every method returns a fact or raises;
    nothing here decides anything.
    """

    def observe(self) -> HostObservation: ...

    def apply_compose(
        self, command: Sequence[str], *, timeout_seconds: int
    ) -> None: ...

    def replace_rules(
        self, family: str, chain: str, rules: Sequence[ingress.FirewallRule]
    ) -> None: ...

    def restore_chains(self, chains: Sequence[ObservedChain]) -> None: ...


@dataclass(slots=True)
class ExposureTransaction:
    """Apply an exposure plan under the lock, then prove it, or put it back.

    The ordering is the contract:

    1. take the product's exclusive deployment lock — an exposure change and a
       deployment must not interleave, because one of them recreates the
       containers the other is measuring;
    2. SNAPSHOT the host before touching it, so a rollback restores an observed
       state rather than a remembered intention;
    3. apply, through a command that can actually change a binding;
    4. RE-OBSERVE. Not "assume the apply worked" — go and look;
    5. verify against the descriptor, and roll back on any refusal.

    Step 2 is a full :class:`HostObservation` rather than a diff because a
    rollback that restores only what it thinks it changed cannot repair what it
    did not notice changing.
    """

    spec: ProductDeploymentSpec
    effects: ExposureEffects
    lock_directory: str | Path = DEFAULT_LOCK_DIR
    timeout_seconds: int = 300
    snapshot: HostObservation | None = field(default=None, init=False)
    report: VerificationReport | None = field(default=None, init=False)
    rolled_back: bool = field(default=False, init=False)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with deployment_lock(
            self.spec.product,
            directory=self.lock_directory,
            label=f"{self.spec.product} exposure",
        ):
            yield

    def run(self, *, command: Sequence[str] = APPLY_COMMAND) -> VerificationReport:
        refuse_non_recreating_apply(command)
        with self._locked():
            self.snapshot = self.effects.observe()
            self.effects.apply_compose(command, timeout_seconds=self.timeout_seconds)
            for family in ingress.FAMILIES:
                rules = tuple(
                    rule
                    for rule in build_firewall_plan(self.spec)
                    if rule.family == family
                )
                self.effects.replace_rules(family, ingress.FILTER_CHAIN[family], rules)
            observed = self.effects.observe()
            report = verify_exposure(self.spec, observed)
            self.report = report
            if not report.ok:
                self._rollback(command)
                raise PreconditionFailed(
                    "the applied exposure did not verify and was rolled back: "
                    + "; ".join(finding.detail for finding in report.refusals)
                )
            return report

    def _rollback(self, command: Sequence[str]) -> None:
        if self.snapshot is None:  # pragma: no cover - run() always snapshots
            raise PreconditionFailed("no snapshot to roll back to")
        self.effects.restore_chains(self.snapshot.chains)
        self.effects.apply_compose(command, timeout_seconds=self.timeout_seconds)
        self.rolled_back = True


def apply_exposure(
    spec: ProductDeploymentSpec,
    effects: ExposureEffects,
    *,
    lock_directory: str | Path = DEFAULT_LOCK_DIR,
    command: Sequence[str] = APPLY_COMMAND,
) -> VerificationReport:
    """One call for the whole transaction, for a caller that wants no state."""
    return ExposureTransaction(
        spec=spec, effects=effects, lock_directory=lock_directory
    ).run(command=command)


def observation_from_text(
    *,
    socket_listing: str = "",
    process_listing: str = "",
    iptables_save: Mapping[str, str] | None = None,
    closed_port_behaviour: str = "unknown",
) -> HostObservation:
    """Build an observation from the raw command output a host produces.

    Here rather than in a provider so a recorded observation — the text an
    operator pasted into an incident channel — can be replayed through the
    same verifier that ran on the host, with no host present.
    """
    chains: list[ObservedChain] = []
    for family, text in sorted((iptables_save or {}).items()):
        chains.extend(parse_iptables_save(text, family=family))
    return HostObservation(
        sockets=parse_socket_listing(socket_listing),
        proxies=parse_docker_proxy_processes(process_listing),
        chains=tuple(chains),
        closed_port_behaviour=closed_port_behaviour,
    )
