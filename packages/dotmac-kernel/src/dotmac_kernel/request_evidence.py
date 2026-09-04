"""One owner for the per-request evidence context: who, from where, under what.

`RequestEvidenceContextV1` is the kernel's implementation of the Foundation's
`request_evidence_context` concern. It answers three questions once per request,
writes all of them, and clears all of them:

* **Who** — an explicit `RequestActor` (kind, id, scopes), including an explicit
  ANONYMOUS actor when nobody is authenticated.
* **From where** — the client address, resolved through a trusted-proxy policy
  rather than read off the socket.
* **Under what correlation** — the request id, believed from an inbound header
  only when the peer is a trusted proxy.

## Extracted product-first from ERP, and what was ported unchanged

ADR-0006's amendment requires shared behaviour be ported from a production
implementation rather than designed here. The trusted-proxy half is a port of
`dotmac_erp:app/net.py` at commit `e286636b`, including the two repairs that
landed with it, and its parity suite came across with it. Both repairs are
BEHAVIOUR, not incidental tidying:

**A bare address derives its prefix from the address family.** ERP's original
appended `/32` to any entry without a prefix, whatever the family, so a bare
`::1` parsed cleanly into `::/32` — 7.9e28 addresses, trusted, silently. It
parsed, so the malformed-entry refusal never saw it, and the failure direction
was OPEN. Neither `32` nor `128` is written down in `parse_trusted_proxy_networks`
below: the width is derived from the address, which is what stops the two
families drifting apart again.

**An explicit prefix is honoured as written.** `2001:db8::/32` must survive. An
implementation that refused every IPv6 entry would satisfy every host-route
assertion above while quietly deleting legitimate configuration, so that
near-miss is part of the parity suite rather than a matter of taste.

## What is deliberately NOT ported

**The environment read.** ERP computes its trusted set from `os.getenv` at
import, so the set cannot change without restarting the process and a test that
sets the variable afterwards is inert. ERP recorded that as a finding for
whichever kernel contract carried the behaviour. It is repaired here: a
`TrustedProxyPolicy` is TYPED CONFIGURATION, constructed by the product and
handed to the middleware. Nothing in this module reads the environment.

**Silent tolerance of a malformed entry.** ERP's original caught the `ValueError`
and `continue`d. That fails CLOSED for forwarded-header trust — a dropped entry
trusts nobody — but it fails SILENTLY for deployment correctness, and it destroys
client provenance: the operator believes they configured a proxy they did not,
and from that moment every consumer reading the client address reads the PROXY's.
Rate limiting, CSRF and the audit trail all read that address. So an entry that
carries characters and does not parse REFUSES, naming the entry.

**JWT decoding.** ERP's middleware decodes a bearer token itself. The kernel has
one authentication seam (`dotmac_kernel.deps.authenticate_request`) and must not
grow a second, so the actor arrives through an `actor_resolver` the product
supplies, or through `bind_actor` once authentication has run. The DEFAULT is
anonymous, written explicitly.

## The defect the shape exists to prevent

ERP's middleware called `actor_id_var.set()` guarded by `if actor_id:` and never
reset anything it set. An anonymous request therefore did not clear the previous
request's actor — it declined to write, and a reader saw whoever was
authenticated before, on that worker, on the previous request. Every audit row,
log line and attribution taken during that request could name the wrong person.

Two rules follow, and neither is a style preference:

**Every field is written on every request, anonymity included.** An anonymous
request WRITES anonymous. Declining to write is what lets a stale actor persist,
and `UNSET_EVIDENCE` is a different fact from an anonymous actor — a reader can
tell "no context has been created" from "a context was created and nobody was
authenticated", which a falsy default cannot express.

**Every `set()` token is retained and reset in `finally`.** `ContextVar.reset`
needs the token from its own `set()`; a blunt reset-to-default would discard
whatever an outer scope had established. `finally` is what carries it across the
exception path, which is where inheritance is most likely, because the failing
request is the one that leaves state behind.

## Pure ASGI, and that is a correctness requirement

This is a bare ASGI callable, not a `BaseHTTPMiddleware`. `BaseHTTPMiddleware`
runs each request in its own anyio task and therefore its own context copy, which
grants contextvar isolation for free — a concurrency proof written against it
passes even when the underlying context handling is broken. ERP measured exactly
that: its concurrency test was green against the unrepaired middleware. Under a
bare ASGI callable the isolation has to come from the `finally` resets below.

## Actor kind is DECLARED, never reconstructed

`RequestActor.kind` is one of `EVIDENCE_ACTOR_KINDS`, derived from the audit
contract's own `ACTOR_TYPES` so the two cannot drift. It is never recovered by
splitting an identifier on a separator — `dotmac_sub:app/services/audit_helpers.py`
does that (`actor_kind = prefix.lower() if separator else None`) and it makes
identity a parsing accident of whatever string a caller happened to send.

Nor is identity inferred from authorization. `scopes` is carried as EVIDENCE and
given no meaning here: this module never asks whether a scope grants anything,
and never derives the actor from one. That matters concretely — the fleet holds
two API-key implementations that DISAGREE about what an empty scope list means
(see `dotmac-kernel/EXTRACTION.toml`), and resolving that disagreement belongs to
the authorization concern, not to the record of what arrived.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from typing import Final
from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send

from dotmac_kernel.audit import ACTOR_TYPES
from dotmac_kernel.logging import request_id_var

__all__ = [
    "ANONYMOUS",
    "ANONYMOUS_KIND",
    "EVIDENCE_ACTOR_KINDS",
    "UNSET_EVIDENCE",
    "RequestActor",
    "RequestActorError",
    "RequestEvidence",
    "RequestEvidenceContextV1",
    "TrustedProxyConfigurationError",
    "TrustedProxyPolicy",
    "bind_actor",
    "current_evidence",
    "parse_trusted_proxy_networks",
]

#: A network the policy holds. The PUBLIC union, deliberately: ERP annotated
#: with `ipaddress._BaseNetwork`, and a private base class is not something a
#: published kernel contract should put in a consumer's type checker.
ProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: What an unauthenticated request WRITES. Not the absence of a write, and not a
#: falsy default: `UNSET_EVIDENCE` below is what "nobody has created a context"
#: looks like, and the two must stay tellable apart at the read site.
ANONYMOUS_KIND: Final[str] = "anonymous"

#: The audit contract's four kinds, plus anonymity. DERIVED from `ACTOR_TYPES`
#: rather than restated, so a fifth audit kind cannot leave this set behind.
#:
#: Anonymity is added here rather than proposed to the audit contract on
#: purpose. `ACTOR_TYPES` answers "who performed this audited operation", and
#: every audit row has an answer — `resolve_audit_actor` is fatal without one.
#: This set answers a wider question, "who does this request appear to be",
#: whose honest answer is sometimes nobody. Widening the audit contract to carry
#: an anonymous actor would be a change to what an audit row may assert, and
#: that is not this module's decision to make.
EVIDENCE_ACTOR_KINDS: Final[frozenset[str]] = frozenset(ACTOR_TYPES | {ANONYMOUS_KIND})

#: Kinds whose `actor_id` is required. `system` is excluded for the same reason
#: `resolve_audit_actor` excludes it: a scheduled job legitimately has no
#: identifier beyond its kind.
_KINDS_REQUIRING_AN_ID: Final[frozenset[str]] = frozenset(
    kind for kind in ACTOR_TYPES if kind != "system"
)


class TrustedProxyConfigurationError(ValueError):
    """A declared trusted-proxy entry cannot be read as an address or network.

    Raised when the policy is CONSTRUCTED — at startup, in the product's own
    composition — so a deployment that mistyped its proxy list does not start.
    The behaviour this replaces was to drop the entry and then serve every
    subsequent request with the proxy's address recorded as the client's.

    The blast direction is deliberate: the process does not start, the health
    check fails and the deploy rolls back, which is recoverable and visible —
    unlike a running deployment that has quietly lost client provenance.
    """


class RequestActorError(ValueError):
    """An actor was declared with a kind, id and scope set that disagree."""


@dataclasses.dataclass(frozen=True, slots=True)
class RequestActor:
    """Who a request appears to be: kind, id and scopes, each stated.

    All three are explicit. `kind` is declared by whoever built the actor and is
    never recovered from `id` by splitting on a separator; `id` is never derived
    from `scopes`; and `scopes` is evidence about what arrived, never a claim
    that anything is permitted.
    """

    kind: str
    id: str | None = None
    scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_ACTOR_KINDS:
            raise RequestActorError(
                f"unknown actor kind {self.kind!r}; expected one of "
                f"{', '.join(sorted(EVIDENCE_ACTOR_KINDS))}. A kind is declared "
                f"by its issuer, never reconstructed by splitting an identifier."
            )
        if self.kind == ANONYMOUS_KIND:
            # An anonymous actor carrying an id or a scope is not anonymous. It
            # is the shape a half-applied authentication leaves behind, and
            # accepting it would let a real principal be recorded as nobody.
            if self.id is not None or self.scopes:
                raise RequestActorError(
                    "an anonymous actor carries no id and no scopes; got "
                    f"id={self.id!r}, scopes={self.scopes!r}"
                )
            return
        identifier = (self.id or "").strip()
        if self.kind in _KINDS_REQUIRING_AN_ID and not identifier:
            raise RequestActorError(
                f"an actor of kind {self.kind!r} needs a non-empty id; only "
                f"'system' and 'anonymous' may go without one"
            )
        if any(not scope.strip() for scope in self.scopes):
            raise RequestActorError(
                f"scopes must be non-empty strings; got {self.scopes!r}"
            )

    @classmethod
    def build(
        cls, kind: str, id: str | None = None, scopes: Iterable[str] = ()
    ) -> RequestActor:
        """Construct with scopes normalised to a sorted, de-duplicated tuple.

        Normalisation is here rather than in `__post_init__` so the dataclass
        stays a faithful record of what it was given: two actors built from the
        same scope set in different orders compare equal, while a hand-built
        `RequestActor` is not silently rewritten behind its author's back.
        """
        return cls(kind=kind, id=id, scopes=tuple(sorted(set(scopes))))


#: The actor an unauthenticated request gets, written explicitly.
ANONYMOUS: Final[RequestActor] = RequestActor(kind=ANONYMOUS_KIND)


@dataclasses.dataclass(frozen=True, slots=True)
class RequestEvidence:
    """The whole per-request record, created once and replaced as a unit.

    One object rather than five loose variables: a partial write is the defect
    this module exists to prevent, and a frozen record cannot be half-written.
    """

    request_id: str
    actor: RequestActor
    client_address: str
    user_agent: str
    #: Whether the peer was inside the configured trusted-proxy set. Recorded
    #: rather than recomputed, so a later reader cannot reach a different answer
    #: than the one the address above was resolved under.
    from_trusted_proxy: bool


#: What "no context has been created" looks like, and it is NOT an anonymous
#: request. A reader that cannot tell the two apart is the read-site half of the
#: inheritance defect: ERP's `""` default made an uncreated context and an
#: unauthenticated one indistinguishable.
UNSET_EVIDENCE: Final[RequestEvidence] = RequestEvidence(
    request_id="",
    actor=RequestActor(kind="system", id="unset"),
    client_address="",
    user_agent="",
    from_trusted_proxy=False,
)

_evidence_var: ContextVar[RequestEvidence] = ContextVar(
    "dotmac_request_evidence", default=UNSET_EVIDENCE
)


def current_evidence() -> RequestEvidence:
    """The evidence for the request in flight, or `UNSET_EVIDENCE` outside one."""
    return _evidence_var.get()


def bind_actor(actor: RequestActor) -> None:
    """Attach an authenticated actor to the request already in flight.

    Authentication runs AFTER this middleware — it needs a database session and
    route dependencies — so the middleware writes an explicit anonymous actor and
    the authenticated one arrives here. This is not a second owner of the
    context: the middleware still created it, and the middleware's `finally`
    still tears it down. `ContextVar.reset(token)` restores the value from before
    the middleware's own `set()` and therefore discards every intervening write,
    whoever made it.

    Raises if no context exists, rather than creating one. A bound actor with no
    request to belong to would live until the next `set()` on that worker, which
    is the leak in the opposite direction.
    """
    evidence = _evidence_var.get()
    if evidence is UNSET_EVIDENCE:
        raise RequestActorError(
            "no request evidence context is in flight; bind_actor attaches an "
            "actor to a context RequestEvidenceContextV1 created, and never "
            "creates one — an actor bound outside a request would outlive it"
        )
    _evidence_var.set(dataclasses.replace(evidence, actor=actor))


def parse_trusted_proxy_networks(declaration: str) -> tuple[ProxyNetwork, ...]:
    """Read a comma-separated proxy declaration, refusing what it cannot parse.

    Empty and separator-only input yields an empty tuple: trusting no proxy is a
    valid configuration, chosen by every deployment that has not configured one,
    and refusing it would refuse them all. An entry that carries characters and
    does not parse raises, naming THAT ENTRY — not the whole declaration, which
    would say only that something in a list is wrong.
    """
    networks: list[ProxyNetwork] = []
    for part in declaration.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            if "/" in value:
                # An explicit prefix is honoured AS WRITTEN. `strict=False`
                # keeps ERP's shipped tolerance for host bits inside a prefixed
                # entry; that is a separate question from the bare case below.
                networks.append(ipaddress.ip_network(value, strict=False))
            else:
                # A BARE address is ONE host, in BOTH families. The width is
                # DERIVED from the address rather than appended, so neither `32`
                # nor `128` appears here and neither family can drift from the
                # other. ERP appended `/32` unconditionally and a bare `::1`
                # became `::/32`, which parsed cleanly and trusted 7.9e28 hosts.
                networks.append(ipaddress.ip_network(value))
        except ValueError as exc:
            raise TrustedProxyConfigurationError(
                f"trusted proxy entry {value!r} is not an IP address or CIDR "
                f"network. An entry that cannot be read is refused rather than "
                f"dropped: a dropped entry trusts nobody, but it leaves every "
                f"client address recorded as the proxy's, and rate limiting, "
                f"CSRF and the audit trail all read that address. Fix the entry "
                f"or remove it — declaring no proxy at all is valid and trusts "
                f"none."
            ) from exc
    return tuple(networks)


def _first_header_value(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(",")[0].strip() or None


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedProxyPolicy:
    """Whether a forwarded address, scheme, host or request id may be believed.

    TYPED CONFIGURATION, constructed by the product and handed in. ERP reads the
    environment at import, so its trusted set cannot change without restarting
    and a test that sets the variable afterwards is inert; ERP recorded that as
    a finding for whichever kernel contract carried the behaviour, and this is
    that contract.

    The default — no networks — trusts no proxy. That is the direction that was
    already closed in ERP and stays closed here: forgetting to configure a proxy
    can never make an attacker-supplied `X-Forwarded-For` authoritative.
    """

    networks: tuple[ProxyNetwork, ...] = ()

    @classmethod
    def from_declaration(cls, declaration: str) -> TrustedProxyPolicy:
        """Build from a product's comma-separated declaration, refusing garbage."""
        return cls(networks=parse_trusted_proxy_networks(declaration))

    @classmethod
    def of(cls, networks: Sequence[ProxyNetwork] | None = None) -> TrustedProxyPolicy:
        """Build from already-parsed networks.

        A bare `str` is REFUSED rather than accepted: iterating one yields
        characters, so `TrustedProxyPolicy.of("10.0.0.1")` would silently build a
        policy of nine unusable entries — or, with `networks=` typed loosely, an
        empty one that trusts nobody while reading as configured. Use
        `from_declaration` for a string.
        """
        if isinstance(networks, str):
            raise TrustedProxyConfigurationError(
                "TrustedProxyPolicy.of takes parsed networks, not a string; a "
                "string iterates as characters. Use "
                "TrustedProxyPolicy.from_declaration(...) instead."
            )
        return cls(networks=tuple(networks or ()))

    def trusts(self, peer_address: str | None) -> bool:
        """Whether this peer is inside the configured set. Absent is not trusted."""
        if not self.networks:
            return False
        if not peer_address:
            return False
        try:
            peer = ipaddress.ip_address(peer_address)
        except ValueError:
            return False
        return any(peer in network for network in self.networks)

    def client_address(
        self, peer_address: str | None, headers: Mapping[str, str]
    ) -> str:
        """The originating client address, believed from headers only via a proxy.

        `X-Forwarded-For` accumulates left to right, so the FIRST hop is the
        original client; taking the last would take the nearest proxy.
        `X-Real-IP` is the fallback, not the preference: the former carries the
        chain, the latter carries one hop's opinion of it.
        """
        if self.trusts(peer_address):
            forwarded_for = _first_header_value(headers.get("x-forwarded-for"))
            if forwarded_for:
                return forwarded_for
            real_ip = _first_header_value(headers.get("x-real-ip"))
            if real_ip:
                return real_ip
        return peer_address or "unknown"

    def scheme(
        self, peer_address: str | None, headers: Mapping[str, str], fallback: str
    ) -> str:
        if self.trusts(peer_address):
            forwarded_proto = _first_header_value(headers.get("x-forwarded-proto"))
            if forwarded_proto:
                return forwarded_proto
        return fallback

    def host(
        self, peer_address: str | None, headers: Mapping[str, str], fallback: str
    ) -> str:
        if self.trusts(peer_address):
            forwarded_host = _first_header_value(headers.get("x-forwarded-host"))
            if forwarded_host:
                return forwarded_host
        return headers.get("host") or fallback


#: Resolves the actor a request arrives with, from the raw ASGI scope. Returning
#: `None` means "nobody was authenticated", which is written as `ANONYMOUS`.
ActorResolver = Callable[[Scope], "RequestActor | None"]

#: Where a pre-middleware component may leave a typed actor for the default
#: resolver. ERP read `request.state.actor_id`; this is the typed form of that.
SCOPE_ACTOR_KEY: Final[str] = "request_actor"


def _scope_state_actor(scope: Scope) -> RequestActor | None:
    """The default resolver: a typed actor left in the scope state, or nobody.

    Only a `RequestActor` is accepted. A bare string is REFUSED rather than
    coerced, because coercing it would mean guessing a kind from an identifier —
    the reconstruction this module exists not to do.
    """
    state = scope.get("state")
    if not isinstance(state, Mapping):
        return None
    candidate = state.get(SCOPE_ACTOR_KEY)
    if candidate is None:
        return None
    if not isinstance(candidate, RequestActor):
        raise RequestActorError(
            f"scope state {SCOPE_ACTOR_KEY!r} must hold a RequestActor, not "
            f"{type(candidate).__name__}. A kind is declared, never inferred "
            f"from an identifier's shape."
        )
    return candidate


def _headers(scope: Scope) -> dict[str, str]:
    """Lower-cased header map, last value wins, decoded latin-1 as ASGI requires."""
    collected: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or ():
        collected[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")
    return collected


def _peer_address(scope: Scope) -> str | None:
    """The transport peer, or `None`. An ASGI scope may legitimately carry none."""
    client = scope.get("client")
    if not client:
        return None
    return str(client[0])


class RequestEvidenceContextV1:
    """Create the per-request evidence context once, and always clear it.

    Bare ASGI on purpose — see this module's docstring. `BaseHTTPMiddleware`
    would isolate each request in its own task and hand the concurrency proof a
    pass it did not earn.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_proxies: TrustedProxyPolicy | None = None,
        actor_resolver: ActorResolver | None = None,
    ) -> None:
        if isinstance(trusted_proxies, str):
            raise TrustedProxyConfigurationError(
                "trusted_proxies takes a TrustedProxyPolicy, not a string; use "
                "TrustedProxyPolicy.from_declaration(...) so a malformed entry "
                "refuses here rather than silently trusting nobody."
            )
        self.app = app
        self.trusted_proxies = trusted_proxies or TrustedProxyPolicy()
        self.actor_resolver = actor_resolver or _scope_state_actor

    def _evidence(self, scope: Scope) -> RequestEvidence:
        """Resolve every field. One place, so none of them can be skipped."""
        headers = _headers(scope)
        peer = _peer_address(scope)
        trusted = self.trusted_proxies.trusts(peer)
        # An inbound correlation id is believed only from a trusted peer.
        # Otherwise any caller could choose the identity its own request is
        # logged under, and collide it with somebody else's deliberately.
        inbound = headers.get("x-request-id") if trusted else None
        return RequestEvidence(
            request_id=inbound or str(uuid4()),
            # EXPLICIT anonymity. Never `if actor:` — declining to write is the
            # defect, because the previous request's value is what survives.
            actor=self.actor_resolver(scope) or ANONYMOUS,
            client_address=self.trusted_proxies.client_address(peer, headers),
            user_agent=headers.get("user-agent", ""),
            from_trusted_proxy=trusted,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        evidence = self._evidence(scope)
        scope.setdefault("state", {})["request_id"] = evidence.request_id
        evidence_token: Token[RequestEvidence] = _evidence_var.set(evidence)
        # Bridged to the existing log formatter, which reads `request_id_var`
        # directly. The evidence record above is the source; this keeps a log
        # line from reading `null` while the two are being reconciled. See this
        # package's CHANGELOG: `middleware.ObservabilityMiddleware` writes the
        # same variable, so the two are ALTERNATIVES, never layers.
        request_id_token: Token[str | None] = request_id_var.set(evidence.request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            # Reset to the token's own prior value, not to a default: a blunt
            # clear would discard whatever an outer scope established, while
            # still discarding a later `bind_actor` write. `finally` is what
            # carries this across the exception path, where a request is most
            # likely to leave state behind.
            request_id_var.reset(request_id_token)
            _evidence_var.reset(evidence_token)
