"""``IngressPolicy.v1`` — the typed exposure contract, and only its mechanics.

This module is the leaf half of the ingress facility: vocabularies, address
normalization, source-level admission, the provider capability matrix, the
derived endpoint token and the two provider-neutral plan shapes. It imports
nothing from this package except :mod:`.errors`, so :mod:`.spec` can use it
while :mod:`.policy` composes both.

## The one boundary that decides what may live here

**This facility owns rendering and enforcement mechanics. It never decides
which environment addresses belong to a source set.** A product declares a
NAMED source set (``source_set = "operations-vpn"``); `dotmac-deployment-control`
resolves that name to addresses at authorization time and freezes the result
into the plan digest. Nothing in this package holds, fetches, defaults or
guesses an address, and :func:`refuse_address_literal` makes a pasted CIDR a
parse error rather than a review question — a literal in a product repository
is a topology fact in Git, and it is exactly the shape that goes stale silently.

The two facts this module refuses to be talked out of, both measured on the
fleet on 2026-08-29:

**1. ``ip6tables`` ``DOCKER-USER`` is INERT.** That chain is jumped only from
``FORWARD``. An IPv6 publish terminates on ``INPUT``, in ``docker-proxy``, so a
v6 rule written into ``DOCKER-USER`` never sees the packet. Two such DROP rules
were found in production doing nothing at all, both with zero packet counters,
while the ports they claimed to close were open from the internet.

The precision matters in both directions, and :data:`FILTER_CHAIN` is where it
lives. ``ip6tables`` CAN filter a published port — in ``INPUT``, with a plain
``--dport``, because nothing DNATs that path. So the capability row says it
enforces source policy and :func:`refuse_inert_chain` refuses the CHAIN rather
than the provider. Recording the provider as incapable would have been as wrong
as recording the DOCKER-USER rule as containment: one error closes a working
control, the other opens a port.

**2. Firewall rules are derived defense-in-depth, never a substitute for a
correct socket binding.** The bind is the control; the rules are the second
layer. That ordering is why ``exposure`` derives ``host_ip`` rather than
accompanying it, and why :func:`firewall_rules_for` always terminates an
allowlist with a DROP — an allowlist whose last rule is an ACCEPT enforces
nothing, which is the third thing that was measured.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Final

from .errors import SpecError

#: The ingress contract's own schema string, versioned INDEPENDENTLY of
#: `ProductDeploymentSpec.v1`. A descriptor schema and a policy schema change
#: for different reasons, and folding them into one string would force a
#: descriptor bump for a rendering fix.
INGRESS_POLICY_SCHEMA: Final = "IngressPolicy.v1"

#: Mandatory, closed, and ordered from least to most reachable so a reader can
#: see that ordering is the point.
#:
#: - ``none``     — no Docker publication exists AT ALL. Not "published to a
#:                  place nobody uses"; no ``ports:`` entry is emitted, so the
#:                  service is reachable only on the compose network.
#: - ``loopback`` — an explicit ``127.0.0.1`` and/or ``::1``, derived.
#: - ``private``  — a declared private or overlay interface address, supplied
#:                  as required promotion-time material and admitted after
#:                  substitution.
#: - ``public``   — routable. Requires TLS, authentication, a named source
#:                  policy, telemetry and an immutable approval locator.
EXPOSURES: Final[tuple[str, ...]] = ("none", "loopback", "private", "public")

#: Mandatory. A short-form Compose publish with no ``host_ip`` spawns one
#: ``docker-proxy`` PER FAMILY, so a descriptor that says nothing about IPv6
#: gets IPv6 anyway. "Say nothing" is therefore not available: the families a
#: publication serves are countable in the descriptor and in the rendered file.
ADDRESS_FAMILIES: Final[tuple[str, ...]] = ("ipv4", "ipv6", "dual_stack")

#: The concrete families, in the fixed order every projection uses.
FAMILIES: Final[tuple[str, ...]] = ("ipv4", "ipv6")

#: What "authenticated" is allowed to mean. Closed, because an open string
#: would let a descriptor claim an authentication no provider implements.
AUTHENTICATIONS: Final[tuple[str, ...]] = ("mtls", "bearer", "oidc", "basic")

#: TLS postures a publication may declare. ``none`` exists so that a
#: ``loopback`` or ``private`` publication can say so explicitly rather than by
#: omission; ``public`` may not use it.
TLS_MODES: Final[tuple[str, ...]] = ("none", "terminate", "passthrough", "mtls")

_SOURCE_SET = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_APPROVAL_REF = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9-]+)+$")
_ROLE_ENV_PREFIX = re.compile(r"[^A-Za-z0-9]+")

#: Loopback literals, per family. Derived rather than declarable: a loopback
#: address a descriptor could WRITE is a loopback address a descriptor could
#: get wrong, and `127.0.0.1` typed as `127.0.0.01` is a parse error in one
#: place and a wildcard in another.
LOOPBACK: Final[dict[str, str]] = {"ipv4": "127.0.0.1", "ipv6": "::1"}


# ── source sets ─────────────────────────────────────────────────────────────


def refuse_address_literal(value: str, *, field: str, where: str) -> None:
    """Refuse anything that parses as an address or network.

    Called on every free-form ingress string a product can write. The refusal
    is deliberately BROADER than "is a valid IP": ``10.0.0.0/8``,
    ``2001:db8::/32`` and a bare ``192.0.2.10`` are all topology, and a
    product repository is the wrong owner for any of them.
    """
    candidate = value.strip().strip("[]")
    if not candidate:
        return
    looks_like_an_address = False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        try:
            ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            pass
        else:
            looks_like_an_address = True
    else:
        looks_like_an_address = True
    if looks_like_an_address:
        raise SpecError(
            f"{field} is an address literal ({value!r}). Ingress declares a "
            "NAMED source set and deployment control resolves it at "
            "authorization; an address in a product descriptor is topology in "
            "Git, and it goes stale without anything failing",
            where=where,
        )


def parse_source_set(value: str, *, field: str, where: str) -> str:
    """Admit one named source set, or refuse."""
    refuse_address_literal(value, field=field, where=where)
    if not _SOURCE_SET.match(value):
        raise SpecError(
            f"{field} must be a lowercase hyphenated source-set NAME matching "
            f"{_SOURCE_SET.pattern} (got {value!r})",
            where=where,
        )
    return value


def parse_approval_ref(value: str, *, where: str) -> str:
    """Admit a policy locator such as ``deployment.public-exposure``.

    A LOCATOR, never an authorization. Foundation can only refuse a shape; the
    decision belongs to `dotmac-approvals` and the binding to
    `dotmac-deployment-control`'s plan digest. Naming the policy inside the
    descriptor is still load-bearing, because the locator is inside the
    digested document — changing which policy a public port claims to need is a
    plan change that needs a fresh approval.
    """
    if not _APPROVAL_REF.match(value):
        raise SpecError(
            "approval_ref must be a dotted policy locator such as "
            f"'deployment.public-exposure' (got {value!r})",
            where=where,
        )
    return value


# ── address normalization and source-level admission ────────────────────────


@dataclass(frozen=True, slots=True)
class AdmittedAddress:
    """One concrete, normalized bind address that passed admission."""

    family: str
    address: str
    is_loopback: bool
    is_wildcard: bool


def normalize_address(value: str, *, where: str) -> AdmittedAddress:
    """Parse and canonicalize ONE concrete address literal.

    Semantic normalization matters because two spellings of the same address
    produce two different digests and two different comparison results:
    ``::1`` and ``0:0:0:0:0:0:0:1`` are the same socket and different strings.
    Everything downstream compares the canonical form.

    Refused here, before family or exposure is considered:

    - an empty value, and a value still carrying ``$``: an unresolved
      interpolation reaching admission means the substitution did not happen,
      and ``"${VM_BIND:-127.0.0.1:}8428"`` is the exact shape that reads as
      loopback and renders as a wildcard;
    - a hostname: a name resolves differently on two hosts and at two times,
      so a bind address that is a name is a bind address nobody can verify;
    - an IPv4-mapped IPv6 address (``::ffff:127.0.0.1``): it is reachable on
      both families through one socket, which makes the family declaration
      unfalsifiable.
    """
    raw = value.strip()
    if not raw:
        raise SpecError("bind address is empty", where=where)
    if "$" in raw or "{" in raw or "}" in raw:
        raise SpecError(
            f"bind address {raw!r} still contains an unresolved expression. "
            "Admission runs AFTER substitution, so an expression here means the "
            "value was never resolved — and an unresolved default is how a "
            "loopback-looking string becomes a wildcard bind",
            where=where,
        )
    candidate = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise SpecError(
            f"bind address {raw!r} is not an IP literal ({exc}). A hostname "
            "resolves differently per host and per moment; a bind nobody can "
            "verify is not a bind",
            where=where,
        ) from exc
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        raise SpecError(
            f"bind address {raw!r} is IPv4-mapped. One socket then answers on "
            "both families, so the declared address_family cannot be checked "
            "against it — declare dual_stack and bind each family explicitly",
            where=where,
        )
    return AdmittedAddress(
        family="ipv6" if parsed.version == 6 else "ipv4",
        address=str(parsed),
        is_loopback=parsed.is_loopback,
        is_wildcard=parsed.is_unspecified,
    )


def admit_bind_address(
    value: str, *, family: str, exposure: str, where: str
) -> AdmittedAddress:
    """Admit a resolved bind address against its DECLARED family and exposure.

    This is the source-level admission gate. It runs on the rendered value
    after promotion-time substitution — at render time for the derived
    loopback case, and again on the host before anything is applied.
    """
    admitted = normalize_address(value, where=where)
    if admitted.is_wildcard:
        raise SpecError(
            f"bind address {value!r} is the wildcard. A wildcard publishes to "
            "every interface the host has and to every interface it grows "
            "later, which is a decision no descriptor made",
            where=where,
        )
    if admitted.family != family:
        raise SpecError(
            f"bind address {value!r} is {admitted.family} but the publication "
            f"declares {family}. A publication that binds a family it did not "
            "declare is the undeclared-family defect wearing a literal",
            where=where,
        )
    if exposure == "loopback" and not admitted.is_loopback:
        raise SpecError(
            f'exposure = "loopback" resolved to {value!r}, which is not a '
            "loopback address",
            where=where,
        )
    if exposure == "private" and admitted.is_loopback:
        raise SpecError(
            f'exposure = "private" resolved to the loopback address {value!r}. '
            "Declare loopback if that is what is wanted; a private exposure "
            "that silently became loopback is an outage, not a hardening",
            where=where,
        )
    return admitted


def bind_material_name(role_code: str, host_port: int, family: str) -> str:
    """The REQUIRED, no-default environment variable a routable bind reads.

    No default, on purpose. A default is precisely what lets a misleading value
    hide the effective bind, so there is nothing to be misled by: an operator
    who supplies nothing gets a refusal from Compose rather than a wildcard.
    """
    prefix = _ROLE_ENV_PREFIX.sub("_", role_code).strip("_").upper()
    return f"{prefix}_{host_port}_BIND_{family.upper()}"


def derived_host_ip(exposure: str, family: str, role_code: str, host_port: int) -> str:
    """The rendered ``host_ip`` for one family of one publication."""
    if exposure == "loopback":
        return LOOPBACK[family]
    return f"${{{bind_material_name(role_code, host_port, family)}:?required}}"


# ── provider capability matrix ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    """What ONE provider can actually enforce, stated so a combination fails
    closed rather than rendering a promise nothing keeps."""

    code: str
    families: frozenset[str]
    protocols: frozenset[str]
    authentications: frozenset[str]
    enforces_source_policy: bool
    enforces_tls: bool
    note: str


#: The matrix. Providers are REPLACEABLE — an edge provider is a transport, not
#: the contract owner — so a capability is a row here rather than an `if` at a
#: call site.
PROVIDERS: Final[dict[str, ProviderCapability]] = {
    "compose": ProviderCapability(
        code="compose",
        families=frozenset(FAMILIES),
        protocols=frozenset({"tcp", "udp"}),
        authentications=frozenset(),
        enforces_source_policy=False,
        enforces_tls=False,
        note=(
            "The publication itself. It decides WHICH SOCKET exists and nothing "
            "else: it cannot authenticate a peer and it cannot filter a source. "
            "It is listed because the socket binding is the primary control, "
            "not because it is a security provider."
        ),
    ),
    "edge_http": ProviderCapability(
        code="edge_http",
        families=frozenset(FAMILIES),
        protocols=frozenset({"tcp"}),
        authentications=frozenset({"mtls", "bearer", "oidc", "basic"}),
        enforces_source_policy=True,
        enforces_tls=True,
        note=(
            "A reverse proxy terminating HTTP. Nginx is the first "
            "implementation and Caddy would be a second; neither is named in a "
            "branch, because an edge is a replaceable transport. It cannot "
            "carry a UDP listener, which is why a UDP publication may not "
            "claim an edge-enforced control."
        ),
    ),
    "service_tls": ProviderCapability(
        code="service_tls",
        families=frozenset(FAMILIES),
        protocols=frozenset({"tcp"}),
        # mTLS ONLY. A workload terminating its own TLS can require a client
        # certificate, which is a property this facility can state. Whether it
        # then checks a bearer token, an OIDC assertion or a password is
        # application behaviour nothing here can see, so crediting the
        # publication with it would be a claim about code the renderer has
        # never read.
        authentications=frozenset({"mtls"}),
        enforces_source_policy=False,
        enforces_tls=True,
        note=(
            "The published workload itself, available only when the "
            "publication declares that it terminates or passes through TLS. It "
            "is a provider in the capability sense and not in the deployment "
            "sense: nothing here configures it, and it is listed so that a "
            "self-terminating mTLS service is expressible without pretending an "
            "edge is in front of it."
        ),
    ),
    "iptables_v4": ProviderCapability(
        code="iptables_v4",
        families=frozenset({"ipv4"}),
        protocols=frozenset({"tcp", "udp"}),
        authentications=frozenset(),
        enforces_source_policy=True,
        enforces_tls=False,
        note=(
            "DOCKER-USER on IPv4, reached from FORWARD after DNAT. Real "
            "containment for a published port, and the reason a rule must match "
            "--ctorigdstport rather than --dport: post-DNAT the destination "
            "port is the CONTAINER port, so a --dport rule written against the "
            "published port matches nothing."
        ),
    ),
    "ip6tables": ProviderCapability(
        code="ip6tables",
        families=frozenset({"ipv6"}),
        protocols=frozenset({"tcp", "udp"}),
        authentications=frozenset(),
        # TRUE, but ONLY in INPUT. The distinction is the entire measured
        # finding: the capability is real and the chain almost everyone reaches
        # for is inert, so recording `False` here would be as wrong as
        # recording the DOCKER-USER rule as containment.
        enforces_source_policy=True,
        enforces_tls=False,
        note=(
            "Able to filter a published port ONLY in INPUT. DOCKER-USER is "
            "jumped only from FORWARD, while an IPv6 publish terminates on "
            "INPUT inside docker-proxy, so a v6 rule in DOCKER-USER never sees "
            "the packet: two production DROP rules written there were found "
            "with zero packet counters while the ports they named were open. "
            "There is also no DNAT on that path, so the INPUT rule matches "
            "--dport on the PUBLISHED port, unlike the IPv4 rule. IPv6 "
            "containment for a published port is still the SOCKET BINDING "
            "first; this provider is the layer behind it."
        ),
    ),
}

#: The chain a v4 published-port allowlist belongs in.
DOCKER_USER_CHAIN: Final = "DOCKER-USER"
#: Where an IPv6 published-port packet actually terminates.
INPUT_CHAIN: Final = "INPUT"

#: The ONLY chain in which a rule for a published port can actually fire, per
#: family. This is a measured table rather than a convention:
#:
#: - IPv4 traffic to a published port is DNATed and traverses ``FORWARD``,
#:   which jumps ``DOCKER-USER``. A rule there fires.
#: - IPv6 traffic to a published port is accepted by ``docker-proxy`` on the
#:   host and therefore terminates on ``INPUT``. ``FORWARD`` is never
#:   traversed, so ``DOCKER-USER`` is never jumped, so a rule there CANNOT
#:   fire — which is why :func:`refuse_inert_chain` exists and why the
#:   ``ip6tables`` capability row reports no source-policy enforcement.
FILTER_CHAIN: Final[dict[str, str]] = {
    "ipv4": DOCKER_USER_CHAIN,
    "ipv6": INPUT_CHAIN,
}

#: How a rule in each chain names the PUBLISHED port. See
#: :meth:`FirewallRule.render` for why these differ; a table rather than a
#: branch so a reader sees both answers at once.
_PORT_MATCH: Final[dict[str, str]] = {
    DOCKER_USER_CHAIN: "-m conntrack --ctorigdstport {port}",
    INPUT_CHAIN: "--dport {port}",
}


def port_match(chain: str, host_port: int) -> str:
    """The correct published-port match for ``chain``."""
    try:
        template = _PORT_MATCH[chain]
    except KeyError:
        raise SpecError(
            f"no published-port match is defined for chain {chain!r}. A rule "
            "written into a chain this table does not describe is a rule whose "
            "correctness nobody has established",
        ) from None
    return template.format(port=host_port)


def providers_for(family: str, protocol: str) -> tuple[str, ...]:
    """Every provider that can act on this family/protocol pair, sorted."""
    return tuple(
        sorted(
            code
            for code, capability in PROVIDERS.items()
            if family in capability.families and protocol in capability.protocols
        )
    )


#: TLS postures under which the workload itself is the enforcing provider.
_SERVICE_ENFORCED_TLS: Final[frozenset[str]] = frozenset(
    {"terminate", "passthrough", "mtls"}
)


def available_providers(
    *, families: tuple[str, ...], protocol: str, tls: str
) -> frozenset[str]:
    """The providers that can act on a DIRECTLY published socket.

    ``edge_http`` is deliberately absent: this is the set for a
    ``[[roles.ports]]`` publication, and a publication the edge already covers
    is refused outright rather than credited with the edge's capabilities.
    ``service_tls`` appears only when the descriptor says the workload
    terminates TLS itself, because that is the only circumstance in which the
    workload can authenticate its own peer.
    """
    conditional = {"edge_http", "service_tls"}
    codes = {
        code
        for family in families
        for code in providers_for(family, protocol)
        if code not in conditional
    }
    if tls in _SERVICE_ENFORCED_TLS and protocol in PROVIDERS["service_tls"].protocols:
        codes.add("service_tls")
    return frozenset(codes)


def capability_refusals(
    *,
    role_code: str,
    host_port: int,
    protocol: str,
    exposure: str,
    families: tuple[str, ...],
    authentication: str,
    source_set: str,
    tls: str,
) -> tuple[str, ...]:
    """Every control this publication CLAIMS that no available provider keeps.

    Fails closed: a claim with no enforcer is a refusal, not a warning. The
    live case this exists for is a raw TCP or UDP publication declaring
    ``authentication = "bearer"`` — an HTTP edge could enforce that and a raw
    published socket cannot, so accepting the string would ship a descriptor
    that reads as authenticated and is not.
    """
    problems: list[str] = []
    where = f"role {role_code!r} port {host_port}"
    available = available_providers(families=families, protocol=protocol, tls=tls)
    if authentication:
        enforcers = sorted(
            code
            for code in available
            if authentication in PROVIDERS[code].authentications
        )
        if not enforcers:
            problems.append(
                f"{where} declares authentication = {authentication!r}, and no "
                f"provider available for protocol {protocol!r} on "
                f"{list(families)} with tls = {tls!r} can enforce it. An HTTP "
                "edge can, and a workload terminating its own TLS can enforce "
                "mTLS; a raw published socket can do neither, and a control "
                "nothing enforces is worse than an absent one because it reads "
                "as present"
            )
    if source_set:
        enforcers = sorted(
            code for code in available if PROVIDERS[code].enforces_source_policy
        )
        if not enforcers:
            problems.append(
                f"{where} declares source_set = {source_set!r} with no provider "
                f"able to enforce it on {list(families)}. On IPv6 this is not a "
                "gap in this matrix — DOCKER-USER is inert for a published "
                "port, so the only IPv6 containment is the socket binding"
            )
        else:
            uncovered = sorted(
                family
                for family in families
                if not {
                    code for code in enforcers if family in PROVIDERS[code].families
                }
            )
            if uncovered:
                problems.append(
                    f"{where} declares source_set = {source_set!r} but no "
                    f"provider can filter it on {uncovered}. A source policy "
                    "that covers one family of a dual-stack publication covers "
                    "the publication on paper and not on the wire"
                )
    if exposure == "public" and protocol == "udp":
        problems.append(
            f'{where} declares exposure = "public" over UDP. No provider in '
            "this matrix terminates TLS or authenticates a UDP peer, so the "
            "controls a public exposure requires cannot exist for it"
        )
    return tuple(problems)


# ── the derived endpoint token ──────────────────────────────────────────────


def endpoint_token(
    *,
    product: str,
    environment: str,
    role: str,
    protocol: str,
    family: str,
    host_port: int,
    exposure: str,
) -> str:
    """The identity an approval covers, DERIVED from the publication.

    Free text is refused as a scope because an approval written for one
    endpoint can be pasted onto another. Every component here comes off the
    publication, the field order is fixed, and the comparison downstream is SET
    EQUALITY rather than containment — containment would let a plan that adds a
    second public port inherit the first one's approval.
    """
    parts = (
        "v1",
        product,
        environment or "-",
        role,
        protocol,
        family,
        str(host_port),
        exposure,
    )
    return "|".join(part.lower() for part in parts)


# ── provider-neutral plans ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FirewallRule:
    """One derived defense-in-depth rule, with its source set UNRESOLVED.

    ``source_set`` names a set; ``argument`` carries the substitution token
    deployment control fills in. This package never holds the members, so a
    rendered plan can live in Git and a resolved one cannot.
    """

    family: str
    chain: str
    protocol: str
    host_port: int
    action: str
    source_set: str
    terminal: bool

    def render(self) -> str:
        """The rule as an ``iptables``/``ip6tables`` argument string.

        The port match differs by chain, and getting it wrong is silent:

        - In ``DOCKER-USER`` the packet has ALREADY been DNATed, so its
          destination port is the CONTAINER port. A remapped publish
          (``9001:5432``) matched with ``--dport 9001`` therefore matches
          nothing at all, while reading in a diff exactly like a rule that
          works. ``--ctorigdstport`` asks conntrack for the ORIGINAL
          destination and is the only correct match there.
        - On the IPv6 ``INPUT`` path there is no DNAT — ``docker-proxy``
          accepts the connection on the published port itself — so the plain
          ``--dport`` is both correct and the only thing that matches.
        """
        parts = [f"-p {self.protocol}", port_match(self.chain, self.host_port)]
        if self.source_set:
            parts.append(f"-s @SOURCE_SET:{self.source_set}@")
        parts.append(f"-j {self.action}")
        return " ".join(parts)


def firewall_rules_for(
    *,
    family: str,
    protocol: str,
    host_port: int,
    source_set: str,
) -> tuple[FirewallRule, ...]:
    """The derived rule set for one family of one publication.

    Always ends in a DROP. An allowlist whose last rule is an ACCEPT enforces
    nothing — everything not matched falls through to the chain policy, which
    on a Docker host is ACCEPT — and that was found in production more than
    once, read by two people as containment.
    """
    if family not in FAMILIES:
        raise SpecError(f"unknown address family {family!r}")
    chain = FILTER_CHAIN[family]
    refuse_inert_chain(family, chain, where=f"port {host_port}")
    rules: list[FirewallRule] = []
    if source_set:
        rules.append(
            FirewallRule(
                family=family,
                chain=chain,
                protocol=protocol,
                host_port=host_port,
                action="ACCEPT",
                source_set=source_set,
                terminal=False,
            )
        )
    rules.append(
        FirewallRule(
            family=family,
            chain=chain,
            protocol=protocol,
            host_port=host_port,
            action="DROP",
            source_set="",
            terminal=True,
        )
    )
    return tuple(rules)


def refuse_inert_chain(family: str, chain: str, *, where: str) -> None:
    """Refuse a rule that would be written where it can never fire."""
    if family == "ipv6" and chain == DOCKER_USER_CHAIN:
        raise SpecError(
            "an IPv6 rule in DOCKER-USER is INERT: that chain is jumped only "
            "from FORWARD, and an IPv6 publish terminates on INPUT inside "
            "docker-proxy. Two such production DROP rules were found with zero "
            "packet counters while the ports were open, and both were read as "
            "containment. Bind the socket instead",
            where=where,
        )


@dataclass(frozen=True, slots=True)
class EdgeEndpoint:
    """One provider-neutral edge route.

    Provider-neutral means every field here is a REQUIREMENT, not a directive:
    an implementation reads ``tls_mode`` and emits whatever its own
    configuration language spells TLS with. Nothing in this shape names a
    provider, so a second implementation is a new renderer rather than a new
    contract.
    """

    host: str
    path: str
    role: str
    upstream_port: int
    tls_mode: str
    tls_policy: str
    authentication: str
    source_set: str
    websocket: bool
    sse: bool
    max_body_bytes: int
    read_timeout_seconds: int
    send_timeout_seconds: int
    security_headers: bool
    redirect_http: bool


__all__ = [
    "ADDRESS_FAMILIES",
    "AUTHENTICATIONS",
    "DOCKER_USER_CHAIN",
    "EXPOSURES",
    "FAMILIES",
    "FILTER_CHAIN",
    "INGRESS_POLICY_SCHEMA",
    "INPUT_CHAIN",
    "LOOPBACK",
    "PROVIDERS",
    "TLS_MODES",
    "AdmittedAddress",
    "EdgeEndpoint",
    "FirewallRule",
    "ProviderCapability",
    "admit_bind_address",
    "available_providers",
    "bind_material_name",
    "capability_refusals",
    "derived_host_ip",
    "endpoint_token",
    "firewall_rules_for",
    "normalize_address",
    "port_match",
    "parse_approval_ref",
    "parse_source_set",
    "providers_for",
    "refuse_address_literal",
    "refuse_inert_chain",
]
