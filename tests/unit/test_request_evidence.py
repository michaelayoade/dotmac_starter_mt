"""The per-request evidence context: ported parity, then the properties it adds.

The trusted-proxy half of `dotmac_kernel.request_evidence` is a PORT of
`dotmac_erp:app/net.py` at commit `e286636b`, and the parity assertions below
came across with it from `dotmac_erp:tests/test_net_trusted_proxy.py` at the
same commit. They are parity tests in the ADR-0006 sense: they record what the
production implementation does, so the extraction can be shown to preserve it.
Tests written only against the port would prove the port agrees with itself.

## Three shapes of proof, and none of them substitutes for another

**Parity.** The ported assertions, including both repairs that landed in ERP
with the code: a bare address is one host in BOTH families (`::1` must not widen
into `::/32`), and an explicit prefix survives as written (`2001:db8::/32`) —
the second is a near-miss guard, because "refuse every IPv6 entry" satisfies
every host-route assertion while silently deleting real configuration.

**Sequential inheritance.** The defect ERP shipped: `actor_id_var.set()` guarded
by `if actor_id:`, and nothing ever reset. An anonymous request declined to
write, so it inherited whoever was authenticated on that worker before it.

**Concurrency.** Real overlap. Stated plainly, because the reverse has already
been believed in this programme: a task-based concurrency proof is NOT what
catches the inheritance defect. `asyncio` runs every task in a copy of the
context, so two requests driven as two tasks are isolated whatever the code
does. ERP measured this against `BaseHTTPMiddleware` and recorded that its
concurrency test was GREEN against the unrepaired middleware.

That claim is not left as prose here.
`test_the_concurrency_proof_alone_would_pass_against_the_broken_shape` drives
the deliberately broken creator below through the same concurrency assertion and
requires it to pass — so if per-task isolation ever stops holding, the comment
does not quietly become false.

The proof that CAN fail under overlap is
`test_a_nested_request_restores_the_outer_requests_evidence`: two requests
overlapping inside ONE context, where the inner request's teardown is the only
thing that can give the outer one its own actor back.

## The sensitivity harness

ADR-0018: a guard that cannot be shown to bite is not a guard, and a check that
passes over a clean tree proves nothing about itself. `_ErpShapedContextCreator`
is the defect, planted: it reproduces ERP's pre-repair shape exactly — writes the
actor only `if` there is one, never resets, and reads the peer address off the
socket instead of through the trusted-origin resolver. Every proof that claims to
catch the inheritance defect is run against it and required to FAIL there, in the
`_ErpShapedContextCreator` section at the foot of this file.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import ipaddress
import textwrap
from collections.abc import MutableMapping
from typing import Any

import pytest
from dotmac_kernel.audit import ACTOR_TYPES
from dotmac_kernel.logging import request_id_var

# `_evidence_var` is imported privately and deliberately: the real creator and
# the planted broken one below must write the SAME variable, or the sensitivity
# proofs would be measuring two different things.
from dotmac_kernel.request_evidence import (
    ANONYMOUS,
    ANONYMOUS_KIND,
    EVIDENCE_ACTOR_KINDS,
    SCOPE_ACTOR_KEY,
    UNSET_EVIDENCE,
    RequestActor,
    RequestActorError,
    RequestEvidence,
    RequestEvidenceContextV1,
    TrustedProxyConfigurationError,
    TrustedProxyPolicy,
    _evidence_var,
    bind_actor,
    current_evidence,
    parse_trusted_proxy_networks,
)

# ── fixtures and scope building ─────────────────────────────────────────────

TRUSTING = TrustedProxyPolicy.from_declaration("203.0.113.0/24")
TRUSTING_NOBODY = TrustedProxyPolicy()


def _scope(
    *,
    client: str | None = "198.51.100.7",
    headers: dict[str, str] | None = None,
    actor: RequestActor | None = None,
) -> dict:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    state: dict = {}
    if actor is not None:
        state[SCOPE_ACTOR_KEY] = actor
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/thing",
        "scheme": "http",
        "server": ("kernel.example", 80),
        "headers": raw,
        "query_string": b"",
        "client": (client, 4321) if client else None,
        "state": state,
    }


async def _noop_receive() -> MutableMapping[str, Any]:  # pragma: no cover
    return {"type": "http.disconnect"}


async def _noop_send(  # pragma: no cover - never awaited
    message: MutableMapping[str, Any],
) -> None:
    return None


def _creator(inner, **kwargs) -> RequestEvidenceContextV1:
    return RequestEvidenceContextV1(inner, **kwargs)


async def _drive(creator, scope: dict, seen: list | None = None) -> None:
    """Run one request through `creator`, capturing what the context held INSIDE."""

    async def inner(scope_, receive, send) -> None:
        if seen is not None:
            seen.append(current_evidence())

    creator.app = inner
    await creator(scope, _noop_receive, _noop_send)


def _run(coro):
    """Drive an async proof without depending on a pytest async plugin.

    This repository carries no async tests today and no `asyncio_mode`
    configuration, so relying on `pytest-asyncio`'s strict-mode marker would make
    these proofs depend on a plugin setting nothing else in the suite exercises.
    `asyncio.run` needs neither.

    ONE CONSEQUENCE, and it decides how every teardown proof below is written.
    `asyncio.run` drives the coroutine as a Task, and a Task runs in a COPY of
    the calling context. So a context variable written inside `_run` is not
    visible after it returns — an assertion about teardown made AFTER the call
    would pass no matter what the code did, which is the same vacuous shape this
    module exists to reject. Every teardown proof therefore makes its
    postcondition INSIDE the coroutine, in the context that was actually
    written. The same fact is why `_sequential_leak_observed` drives both of its
    requests within a single `_run`: two separate calls would be two contexts,
    and the leak could not appear even in code that has it.
    """
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_context():
    """Each test starts from a known outer context and restores it.

    Not merely tidiness. ERP recorded that its own suite leaked an actor between
    tests through exactly this defect class, and a postcondition asserting an
    empty value then failed against CORRECT code. A test suite that inherits
    state cannot measure a fix for inherited state.
    """
    sentinel = RequestEvidence(
        request_id="outer-request",
        actor=RequestActor(kind="user", id="outer-actor"),
        client_address="192.0.2.1",
        user_agent="outer-agent",
        from_trusted_proxy=False,
    )
    evidence_token = _evidence_var.set(sentinel)
    request_id_token = request_id_var.set("outer-request")
    try:
        yield sentinel
    finally:
        request_id_var.reset(request_id_token)
        _evidence_var.reset(evidence_token)


# ── PARITY: the closed default ──────────────────────────────────────────────


def test_no_configured_network_trusts_no_proxy():
    """The default an unconfigured deployment gets, and it is the safe one.

    PORTED from `dotmac_erp:tests/test_net_trusted_proxy.py`. This is the
    direction that was already closed in ERP: forgetting to declare a proxy can
    never make an attacker-supplied `X-Forwarded-For` authoritative.
    """
    assert TRUSTING_NOBODY.trusts("203.0.113.9") is False


def test_an_untrusted_peer_cannot_supply_a_client_address():
    """PORTED. The header is present, well-formed and ignored."""
    assert (
        TRUSTING_NOBODY.client_address("198.51.100.7", {"x-forwarded-for": "1.2.3.4"})
        == "198.51.100.7"
    )


def test_an_untrusted_peer_cannot_supply_a_scheme_or_host():
    """PORTED."""
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "attacker.example",
        "host": "kernel.example",
    }
    assert TRUSTING_NOBODY.scheme("198.51.100.7", headers, "http") == "http"
    assert (
        TRUSTING_NOBODY.host("198.51.100.7", headers, "kernel.example")
        == "kernel.example"
    )


# ── PARITY: the open case, and it is open only to a configured peer ─────────


def test_a_trusted_peer_is_recognised():
    """PORTED."""
    assert TRUSTING.trusts("203.0.113.9") is True


def test_a_peer_outside_the_configured_network_is_not():
    """PORTED. SENSITIVITY: without this the fixture could trust everything and
    every assertion above would still pass."""
    assert TRUSTING.trusts("198.51.100.7") is False


def test_a_trusted_peer_supplies_the_client_address():
    """PORTED."""
    assert (
        TRUSTING.client_address("203.0.113.9", {"x-forwarded-for": "1.2.3.4"})
        == "1.2.3.4"
    )


def test_the_first_forwarded_hop_wins():
    """PORTED. `X-Forwarded-For` accumulates left to right, so the original
    client is first; taking the last would take the nearest proxy."""
    assert (
        TRUSTING.client_address(
            "203.0.113.9",
            {"x-forwarded-for": "1.2.3.4, 203.0.113.9, 203.0.113.10"},
        )
        == "1.2.3.4"
    )


def test_x_real_ip_is_the_fallback_not_the_preference():
    """PORTED. Both present: `X-Forwarded-For` decides. It carries the chain;
    `X-Real-IP` carries one hop's opinion of it."""
    assert (
        TRUSTING.client_address(
            "203.0.113.9", {"x-forwarded-for": "1.2.3.4", "x-real-ip": "9.9.9.9"}
        )
        == "1.2.3.4"
    )
    assert TRUSTING.client_address("203.0.113.9", {"x-real-ip": "9.9.9.9"}) == "9.9.9.9"


def test_a_trusted_peer_supplies_scheme_and_host():
    """PORTED."""
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "public.example",
        "host": "kernel.example",
    }
    assert TRUSTING.scheme("203.0.113.9", headers, "http") == "https"
    assert TRUSTING.host("203.0.113.9", headers, "kernel.example") == "public.example"


# ── PARITY: degenerate inputs, none of which may open the gate ──────────────


def test_a_request_with_no_client_is_not_trusted():
    """PORTED. An ASGI scope may carry no client at all. Absent is not trusted."""
    assert TRUSTING.trusts(None) is False
    assert TRUSTING.client_address(None, {}) == "unknown"


def test_an_unparseable_peer_address_is_not_trusted():
    """PORTED."""
    assert TRUSTING.trusts("not-an-address") is False


def test_an_empty_forwarded_header_falls_back_to_the_peer():
    """PORTED."""
    assert (
        TRUSTING.client_address("203.0.113.9", {"x-forwarded-for": "  "})
        == "203.0.113.9"
    )


# ── PARITY: both repairs that landed with the ERP code ──────────────────────


def test_a_bare_address_is_parsed_as_a_single_host_network():
    """PORTED, INCLUDING ITS WIDENING. The cases are as wide as the name.

    This test asserted IPv4 only in ERP while claiming a family-neutral
    property, and that gap is exactly how the fail-open defect survived: a
    reader checking whether bare addresses were handled found a test with the
    right name and stopped.
    """
    v4 = parse_trusted_proxy_networks("10.0.0.1, 10.1.0.0/16")
    assert ipaddress.ip_address("10.0.0.1") in v4[0]
    assert ipaddress.ip_address("10.1.2.3") in v4[1]
    assert ipaddress.ip_address("10.0.0.2") not in v4[0]

    v6 = parse_trusted_proxy_networks("::1, 2001:db8::1, 2001:db8::/32")
    assert [str(n) for n in v6] == ["::1/128", "2001:db8::1/128", "2001:db8::/32"]
    assert ipaddress.ip_address("::1") in v6[0]
    assert ipaddress.ip_address("2001:db8::1") in v6[1]
    # THE NEAR-MISS. An EXPLICIT prefix is honoured as written. Without this the
    # repair could have been "refuse every IPv6 entry", which satisfies every
    # host-route assertion above and quietly removes a legitimate configuration.
    assert ipaddress.ip_address("2001:db8:ffff::9") in v6[2]


def test_a_bare_ipv6_address_cannot_widen_into_a_supernet():
    """PORTED. THE FAIL-OPEN REPAIR, and it is the opposite direction from the
    refusal below.

    ERP's rule appended `/32` to any entry without a prefix, whatever the
    family, so a bare `::1` became `::/32` — 79 228 162 514 264 337 593 543 950
    336 addresses — and `strict=False` masked the host bits without complaint.
    It parsed CLEANLY, so the malformed-entry refusal never saw it.

    SENSITIVITY. Each address below sits INSIDE the old `::/32` and OUTSIDE the
    correct `::1/128`, so each assertion fails against the previous rule. The
    first assertion would fail on its own; only these show what the difference
    actually admitted.
    """
    parsed = parse_trusted_proxy_networks("::1")
    assert str(parsed[0]) == "::1/128"
    assert parsed[0].num_addresses == 1
    for trusted_by_the_old_rule in ("::2", "0:0:1::", "::ffff:1"):
        assert ipaddress.ip_address(trusted_by_the_old_rule) not in parsed[0], (
            f"{trusted_by_the_old_rule} is inside ::/32; a bare ::1 must be a "
            f"host route, not a supernet"
        )


def test_neither_prefix_width_is_written_down_in_the_parser():
    """STRUCTURAL, and it is the reason the repair holds rather than the repair.

    Deriving the width from the address is what stops the two families drifting
    apart AGAIN. An implementation that appended `/32` for IPv4 and `/128` for
    IPv6 would satisfy every behavioural assertion above and reintroduce the
    exact shape that failed: two literals, one branch, and nothing forcing a
    later editor to change both.

    AST, not a text scan — the module docstring names `/32` and `::/32` on
    purpose, to explain the defect, and a substring check would refuse its own
    rationale.
    """
    from dotmac_kernel import request_evidence

    source = inspect.getsource(request_evidence.parse_trusted_proxy_networks)
    tree = ast.parse(textwrap.dedent(source))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in (32, 128, "/32", "/128")
    ]
    assert not literals, (
        f"the parser writes a prefix width down ({literals}); derive it from "
        f"the address family instead, so the two families cannot drift"
    )


def test_a_trusted_ipv6_peer_is_recognised_through_the_request_path():
    """PORTED. The host route reaches the actual decision, not just the parser.

    A parser returning `::1/128` proves nothing about `trusts` unless the two
    are wired together. Both directions: the host itself is trusted, and its
    neighbour — trusted under the old `::/32` — is not.
    """
    policy = TrustedProxyPolicy.from_declaration("::1")
    assert policy.trusts("::1") is True
    assert policy.trusts("::2") is False
    assert policy.client_address("::1", {"x-forwarded-for": "1.2.3.4"}) == "1.2.3.4"
    assert policy.client_address("::2", {"x-forwarded-for": "1.2.3.4"}) == "::2"


# ── PARITY: a malformed entry refuses, and an absent one does not ───────────
#
# SENSITIVITY, both directions, and the PAIR is the proof.
#
# The DEFECT is planted in `test_a_malformed_entry_refuses_loudly`: an entry
# that carries characters and does not parse. ERP's original caught the
# `ValueError` and `continue`d, so it returned a list and NEVER raised.
#
# The NEAR-MISS is planted in `test_declaring_no_proxy_at_all_stays_valid`:
# empty and separator-only input. A guard that refused every input it could not
# turn into a network would fire there too, and would refuse the default
# configuration of every deployment that has not declared a proxy. It must NOT
# be named. Keeping both is what distinguishes "declared garbage" from
# "declared nothing", which are different acts with different correct answers.


def test_a_malformed_entry_refuses_loudly():
    """PORTED. THE REPAIR, and the direction of the failure is the point.

    A dropped entry FAILS CLOSED for forwarded-header trust: it trusts nobody,
    so no header is honoured that would not have been anyway. What it fails at
    is DEPLOYMENT CORRECTNESS and CLIENT PROVENANCE — the operator believes they
    configured a proxy they did not, and from then on every downstream client
    address is the proxy's. This refuses for provenance, not because trust would
    otherwise leak.
    """
    with pytest.raises(TrustedProxyConfigurationError):
        parse_trusted_proxy_networks("nonsense, 10.1.0.0/16")


def test_a_malformed_entry_refuses_even_when_it_is_the_only_one():
    """PORTED. Without this, an implementation that refused only a PARTIALLY
    parseable list — and quietly returned `()` for a wholly malformed one —
    would satisfy the test above while leaving the worst case silent."""
    with pytest.raises(TrustedProxyConfigurationError):
        parse_trusted_proxy_networks("172.16.0.0/99")


def test_the_refusal_names_the_offending_entry():
    """PORTED. The message has to be actionable at 3am, and must name the ENTRY.

    Naming the whole declaration would say only that something in a
    comma-separated list is wrong.
    """
    with pytest.raises(TrustedProxyConfigurationError) as raised:
        parse_trusted_proxy_networks("10.0.0.0/8, 172.16.0.0/99, 127.0.0.1")
    assert "172.16.0.0/99" in str(raised.value)


def test_declaring_no_proxy_at_all_stays_valid():
    """PORTED. THE NEAR-MISS, and it must NOT be named.

    Every deployment that has not declared a proxy reaches this path on every
    boot. Separator-only and whitespace-only input are the same act written
    untidily.
    """
    assert parse_trusted_proxy_networks("") == ()
    assert parse_trusted_proxy_networks("   ") == ()
    assert parse_trusted_proxy_networks(" , ,, ") == ()


def test_erps_committed_production_value_still_parses():
    """PORTED. The regression the refusal could plausibly cause: refusing a
    deployment that was correct all along.

    This is the exact pair `dotmac_erp:deploy/product.toml`'s `[ingress]
    trusted_proxies` declares and its `docker-compose.yml` sets. It is carried
    across so the port cannot refuse the first adopter's live configuration.
    """
    parsed = parse_trusted_proxy_networks("172.16.0.0/12,127.0.0.1")
    assert [str(n) for n in parsed] == ["172.16.0.0/12", "127.0.0.1/32"]


# ── NOT ported: the environment read is gone, and configuration is typed ────


def test_the_module_never_reads_the_environment():
    """The finding ERP recorded for whichever kernel contract carried this.

    ERP computes its trusted set from `os.getenv` at IMPORT, so the set cannot
    change without restarting the process and a `monkeypatch.setenv` after
    import is inert — ERP's own parity tests had to patch the parsed constant
    and say so. A policy that is typed configuration has no such seam.

    AST over the whole module, so a `getenv` reached through any name is caught,
    and so the docstring may keep explaining why the read is absent.
    """
    from dotmac_kernel import request_evidence

    source = inspect.getsource(request_evidence)
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Import) and any(a.name == "os" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "os")
        or (isinstance(node, ast.Attribute) and node.attr in {"getenv", "environ"})
        or (isinstance(node, ast.Name) and node.id in {"getenv", "environ"})
    ]
    assert not offenders, (
        f"the evidence context reads the environment at lines {offenders}; the "
        f"trusted set is typed configuration a product constructs and hands in"
    )


def test_a_string_is_refused_where_parsed_networks_are_expected():
    """A string iterates as CHARACTERS.

    `TrustedProxyPolicy.of("10.0.0.1")` would otherwise build a policy of nine
    unusable entries, or an empty one that reads as configured and trusts
    nobody — the silent-misconfiguration shape the refusal above exists to end,
    arriving through the other door.
    """
    with pytest.raises(TrustedProxyConfigurationError):
        TrustedProxyPolicy.of("10.0.0.1")  # type: ignore[arg-type]
    with pytest.raises(TrustedProxyConfigurationError):
        RequestEvidenceContextV1(None, trusted_proxies="10.0.0.1")  # type: ignore[arg-type]


def test_parsed_networks_are_accepted_where_a_string_is_refused():
    """SENSITIVITY for the refusal above: it must reject the WRONG TYPE, not
    every construction. Without this, `of` could raise unconditionally."""
    policy = TrustedProxyPolicy.of([ipaddress.ip_network("10.0.0.0/8")])
    assert policy.trusts("10.1.2.3") is True
    assert TrustedProxyPolicy.of().trusts("10.1.2.3") is False


# ── the actor is DECLARED, never reconstructed ──────────────────────────────


def test_the_evidence_kinds_are_the_audit_kinds_plus_anonymity():
    """One vocabulary owner. `EVIDENCE_ACTOR_KINDS` is DERIVED from the audit
    contract's `ACTOR_TYPES`, so a fifth audit kind cannot leave it behind.

    Anonymity is the single addition, and it is added HERE rather than to the
    audit contract deliberately: `ACTOR_TYPES` answers "who performed this
    audited operation" and every audit row has an answer, while this set answers
    "who does this request appear to be", whose honest answer is sometimes
    nobody.
    """
    assert EVIDENCE_ACTOR_KINDS == frozenset(ACTOR_TYPES | {ANONYMOUS_KIND})
    assert ANONYMOUS_KIND not in ACTOR_TYPES


def test_an_unknown_actor_kind_is_refused():
    """A kind outside the contract is a contract change, not a call-site string."""
    with pytest.raises(RequestActorError):
        RequestActor(kind="robot", id="r-1")


def test_an_anonymous_actor_carries_no_identity_and_no_scope():
    """An anonymous actor with an id is not anonymous — it is the shape a
    half-applied authentication leaves behind, and accepting it would record a
    real principal as nobody."""
    assert ANONYMOUS.kind == ANONYMOUS_KIND
    assert ANONYMOUS.id is None
    assert ANONYMOUS.scopes == ()
    with pytest.raises(RequestActorError):
        RequestActor(kind=ANONYMOUS_KIND, id="alice")
    with pytest.raises(RequestActorError):
        RequestActor(kind=ANONYMOUS_KIND, scopes=("billing:read",))


def test_an_identified_kind_needs_an_identifier():
    """Matching the audit contract's own rule: `system` may go without one,
    because a scheduled job legitimately has no identifier beyond its kind."""
    with pytest.raises(RequestActorError):
        RequestActor(kind="user", id="   ")
    assert RequestActor(kind="system").id is None


def test_a_bare_identifier_in_scope_state_is_refused_not_split():
    """THE ANTI-PATTERN, named so it cannot be carried forward.

    `dotmac_sub:app/services/audit_helpers.py` recovers a kind by splitting an
    identifier on a separator (`actor_kind = prefix.lower() if separator else
    None`), which makes identity a parsing accident of whatever string a caller
    happened to send. A bare string here is REFUSED rather than coerced,
    because coercing it would mean guessing the kind.
    """
    scope = _scope()
    scope["state"][SCOPE_ACTOR_KEY] = "api_key:abc123"
    creator = _creator(None, trusted_proxies=TRUSTING_NOBODY)
    with pytest.raises(RequestActorError):
        _run(_drive(creator, scope))


def test_identity_is_not_derived_from_scopes_nor_scopes_from_identity():
    """`dotmac_sub:app/api/crm.py` identifies its caller by the PRESENCE of an
    `integration:crm` scope, so identity becomes a side effect of authorization.

    Here the three facts are independent: two actors of different kinds may
    carry identical scopes, and one kind may carry none. This module also never
    asks whether a scope grants anything — which matters concretely, because the
    fleet holds two API-key implementations that disagree about what an EMPTY
    scope list means, and that disagreement belongs to authorization, not to the
    record of what arrived.
    """
    machine = RequestActor.build("api_key", "key-1", ["integration:crm"])
    person = RequestActor.build("user", "alice", ["integration:crm"])
    assert machine.kind != person.kind
    assert machine.scopes == person.scopes
    assert RequestActor.build("api_key", "key-2", []).scopes == ()


def test_build_normalises_scopes_but_the_record_is_not_rewritten():
    """`build` sorts and de-duplicates so two equal scope sets compare equal; a
    hand-built record is left exactly as its author wrote it."""
    assert RequestActor.build("user", "a", ["b", "a", "b"]).scopes == ("a", "b")
    assert RequestActor(kind="user", id="a", scopes=("b", "a")).scopes == ("b", "a")


# ── every field, every request ──────────────────────────────────────────────


def test_an_anonymous_request_writes_anonymous():
    """Not silence, and not a falsy default. Declining to write is the defect."""
    seen: list[RequestEvidence] = []
    _run(_drive(_creator(None, trusted_proxies=TRUSTING_NOBODY), _scope(), seen))
    assert seen[0].actor == ANONYMOUS


def test_an_uncreated_context_is_distinguishable_from_an_anonymous_one():
    """The read-site half of the defect.

    ERP's `""` default made "no context has been created" and "a context was
    created and nobody was authenticated" the same value at every read site.
    They are different facts and a reader must be able to tell them apart.
    """
    assert UNSET_EVIDENCE.actor != ANONYMOUS
    assert UNSET_EVIDENCE.request_id == ""
    seen: list[RequestEvidence] = []
    _run(_drive(_creator(None, trusted_proxies=TRUSTING_NOBODY), _scope(), seen))
    assert seen[0] != UNSET_EVIDENCE
    assert seen[0].request_id


def test_every_field_is_written_on_every_request():
    """One resolution site, so no field can be skipped.

    A partial write is the defect: whichever field is skipped keeps the previous
    request's value, and the skipped field is the one nobody is looking at.
    """
    seen: list[RequestEvidence] = []
    _run(
        _drive(
            _creator(None, trusted_proxies=TRUSTING_NOBODY),
            _scope(headers={"user-agent": "probe/1.0"}),
            seen,
        )
    )
    evidence = seen[0]
    assert evidence.request_id
    assert evidence.actor == ANONYMOUS
    assert evidence.client_address == "198.51.100.7"
    assert evidence.user_agent == "probe/1.0"
    assert evidence.from_trusted_proxy is False


def test_the_request_id_bridge_agrees_with_the_evidence_record():
    """`JsonLogFormatter` reads `request_id_var` directly, so the two must not
    disagree inside a request. The evidence record is the source; the variable
    is a bridge kept in step with it."""
    seen: list[str] = []

    async def inner(scope, receive, send):
        seen.append(request_id_var.get() or "")
        seen.append(current_evidence().request_id)

    creator = _creator(inner, trusted_proxies=TRUSTING_NOBODY)
    _run(creator(_scope(), _noop_receive, _noop_send))
    assert seen[0] == seen[1] != ""


# ── untrusted input cannot become authoritative ─────────────────────────────


def test_an_untrusted_peer_cannot_choose_the_request_id():
    """Any caller could otherwise pick the correlation identity its own request
    is logged under, and collide it with somebody else's deliberately."""
    seen: list[RequestEvidence] = []
    _run(
        _drive(
            _creator(None, trusted_proxies=TRUSTING_NOBODY),
            _scope(headers={"x-request-id": "attacker-chosen"}),
            seen,
        )
    )
    assert seen[0].request_id != "attacker-chosen"
    assert seen[0].request_id


def test_a_trusted_peer_may_supply_the_request_id():
    """SENSITIVITY. Without this the assertion above would pass against a
    creator that ignored the header unconditionally, which is different
    behaviour that happens to satisfy the same test."""
    seen: list[RequestEvidence] = []
    _run(
        _drive(
            _creator(None, trusted_proxies=TRUSTING),
            _scope(client="203.0.113.9", headers={"x-request-id": "edge-abc"}),
            seen,
        )
    )
    assert seen[0].request_id == "edge-abc"


def test_an_untrusted_forwarded_address_cannot_become_the_client_address():
    seen: list[RequestEvidence] = []
    _run(
        _drive(
            _creator(None, trusted_proxies=TRUSTING_NOBODY),
            _scope(client="198.51.100.7", headers={"x-forwarded-for": "1.2.3.4"}),
            seen,
        )
    )
    assert seen[0].client_address == "198.51.100.7"
    assert seen[0].from_trusted_proxy is False


def test_a_trusted_forwarded_address_is_used():
    """That the resolver is reached AT ALL. ERP's middleware read
    `request.client.host` directly and bypassed its own correct resolver."""
    seen: list[RequestEvidence] = []
    _run(
        _drive(
            _creator(None, trusted_proxies=TRUSTING),
            _scope(client="203.0.113.9", headers={"x-forwarded-for": "1.2.3.4"}),
            seen,
        )
    )
    assert seen[0].client_address == "1.2.3.4"
    assert seen[0].from_trusted_proxy is True


# ── teardown: the sequential defect, and what token reset means ─────────────


def _sequential_leak_observed(creator) -> bool:
    """Drive an authenticated request, then an anonymous one, on one worker.

    Returns whether the anonymous request saw the first request's actor. Shared
    by the real proof and by its sensitivity twin at the foot of this file, so
    the two cannot drift into measuring different things.
    """

    async def both() -> RequestEvidence:
        seen: list[RequestEvidence] = []
        await _drive(creator, _scope(actor=RequestActor(kind="user", id="alice")), seen)
        assert seen[0].actor.id == "alice"
        seen.clear()
        await _drive(creator, _scope(), seen)
        return seen[0]

    return _run(both()).actor.id == "alice"


def test_an_anonymous_request_after_an_authenticated_one_is_anonymous():
    """THE REGRESSION, and the proof that catches it.

    This is the bug exactly as ERP shipped it: the second request saw `alice`,
    because `if actor_id:` declined to write and nothing had reset. It is a
    SEQUENTIAL proof on purpose — see
    `test_the_sequential_proof_bites_the_shape_it_was_written_for`, which
    requires this same measurement to come out the other way against the broken
    creator, and `test_the_concurrency_proof_alone_would_pass_against_the_broken_shape`,
    which shows a concurrency proof would not have caught it.
    """
    creator = _creator(None, trusted_proxies=TRUSTING_NOBODY)
    assert not _sequential_leak_observed(
        creator
    ), "an anonymous request inherited the previous request's actor"


def test_the_context_is_restored_to_the_outer_value_after_the_request(
    _isolated_context,
):
    """`reset(token)` restores the value from BEFORE this creator's `set()`.

    Not a blunt clear to a default, and the difference is load-bearing: forcing
    a default would discard whatever an outer scope established.

    The postcondition is taken INSIDE the coroutine — see `_run`. Taken after
    it, it would be read from a context the middleware never touched and would
    hold against any implementation at all.
    """

    async def body() -> RequestEvidence:
        creator = _creator(None, trusted_proxies=TRUSTING_NOBODY)
        await _drive(creator, _scope(actor=RequestActor(kind="user", id="alice")))
        return current_evidence()

    assert _run(body()) == _isolated_context


def test_the_request_actor_does_not_survive_the_request(_isolated_context):
    """The half the restoration assertion does not make on its own.

    Restoring an outer value and never writing the request's actor at all would
    both satisfy the test above. This requires `alice` to have been genuinely
    visible INSIDE the request and genuinely gone after it.
    """

    async def body() -> tuple[str | None, str | None]:
        seen: list[RequestEvidence] = []
        creator = _creator(None, trusted_proxies=TRUSTING_NOBODY)
        await _drive(creator, _scope(actor=RequestActor(kind="user", id="alice")), seen)
        return seen[0].actor.id, current_evidence().actor.id

    inside, after = _run(body())
    assert inside == "alice"
    assert after != "alice"


def test_the_context_is_restored_when_the_request_raises(_isolated_context):
    """The path where inheritance is most likely, because the failing request is
    the one that leaves state behind."""

    async def exploding(scope, receive, send):
        raise RuntimeError("boom")

    async def body() -> RequestEvidence:
        creator = _creator(exploding, trusted_proxies=TRUSTING_NOBODY)
        with pytest.raises(RuntimeError):
            await creator(
                _scope(actor=RequestActor(kind="user", id="alice")),
                _noop_receive,
                _noop_send,
            )
        return current_evidence()

    assert _run(body()) == _isolated_context


def test_an_actor_bound_after_authentication_is_still_discarded(_isolated_context):
    """Why TOKEN reset rather than a forced clear, stated as a test.

    Authentication runs after this creator — it needs a session and route
    dependencies — so `bind_actor` writes the real actor mid-request.
    `reset(token)` restores the value from before the creator's own `set()` and
    discards every intervening write, whoever made it.

    A blunt clear would also discard it, and would ADDITIONALLY discard the
    outer value, which the test above forbids. Both together pin the semantics;
    either alone admits the wrong fix.
    """

    async def body() -> tuple[RequestEvidence, RequestEvidence]:
        seen: list[RequestEvidence] = []

        async def authenticates(scope, receive, send):
            bind_actor(RequestActor.build("user", "alice", ["billing:read"]))
            seen.append(current_evidence())

        creator = _creator(authenticates, trusted_proxies=TRUSTING_NOBODY)
        await creator(_scope(), _noop_receive, _noop_send)
        return seen[0], current_evidence()

    inside, after = _run(body())
    assert inside.actor.id == "alice"
    assert inside.request_id  # the rest of the record survived the replacement
    assert after == _isolated_context


def test_binding_an_actor_outside_a_request_is_refused():
    """The leak in the opposite direction: an actor bound with no request to
    belong to would live on that worker until the next `set()`."""
    outer = _evidence_var.set(UNSET_EVIDENCE)
    try:
        with pytest.raises(RequestActorError):
            bind_actor(RequestActor(kind="user", id="alice"))
    finally:
        _evidence_var.reset(outer)


# ── concurrency: real overlap, and what each proof can and cannot catch ─────


def _nested_overlap_preserves_the_outer_actor(creator_factory) -> bool:
    """Two requests overlapping inside ONE context, innermost finishing first.

    This is the overlap shape that CAN fail. Unlike two tasks — which `asyncio`
    hands separate context copies — both requests here run in the same context,
    so the INNER request's teardown is the only thing that can give the outer
    request its own actor back.

    `creator_factory` builds both, so the sensitivity twin can plant the broken
    creator on the inside, which is where the teardown that matters happens.
    """

    async def nested() -> str | None:
        observed: list[str | None] = []

        async def outer_body(scope, receive, send):
            inner = creator_factory(None)
            await _drive(inner, _scope(actor=RequestActor("user", "bob")))
            observed.append(current_evidence().actor.id)

        await creator_factory(outer_body)(
            _scope(actor=RequestActor("user", "alice")), _noop_receive, _noop_send
        )
        return observed[0]

    return _run(nested()) == "alice"


def test_a_nested_request_restores_the_outer_requests_evidence():
    """THE OVERLAP PROOF THAT CAN FAIL.

    A concurrency test built from tasks cannot fail here — see the pair below —
    so this one deliberately overlaps two requests inside a single context.
    """
    assert _nested_overlap_preserves_the_outer_actor(
        lambda inner: _creator(inner, trusted_proxies=TRUSTING_NOBODY)
    ), "an inner request's teardown did not restore the outer request's actor"


def _concurrent_actors_stay_separate(creator_factory) -> dict[str, str | None]:
    """Two requests genuinely in flight at once, each parked until both entered."""

    async def race() -> dict[str, str | None]:
        both_entered = asyncio.Event()
        entered: list[str] = []
        observed: dict[str, str | None] = {}

        async def drive(name: str) -> None:
            async def inner(scope, receive, send):
                entered.append(name)
                if len(entered) == 2:
                    both_entered.set()
                await both_entered.wait()
                # Read AFTER the other request has written its own values.
                observed[name] = current_evidence().actor.id

            await creator_factory(inner)(
                _scope(actor=RequestActor("user", name)), _noop_receive, _noop_send
            )

        await asyncio.gather(drive("alice"), drive("bob"))
        return observed

    return _run(race())


def test_concurrent_requests_cannot_inherit_each_others_actor():
    """Real overlap: both requests are in flight at once, each parked until the
    other has also entered.

    Read the pair below before treating this as the proof of the repair. It is
    NOT. It is a guard on an ASSUMPTION — that whatever runs these requests
    keeps their contexts apart — and that assumption is currently supplied by
    `asyncio`, not by this module.
    """
    observed = _concurrent_actors_stay_separate(
        lambda inner: _creator(inner, trusted_proxies=TRUSTING_NOBODY)
    )
    assert observed == {
        "alice": "alice",
        "bob": "bob",
    }, "a concurrent request saw another's actor: contexts are shared"


# ── the sensitivity harness: the defect, planted ────────────────────────────


class _ErpShapedContextCreator:
    """ERP's pre-repair shape, planted so the proofs above can be shown to bite.

    Reproduced faithfully and for one purpose. All three defects are here:

    1. The actor is written only `if` there is one, so an anonymous request
       DECLINES to write and inherits whatever was there.
    2. Nothing is ever reset, so what it writes outlives the request.
    3. The peer address is read straight off the socket, bypassing the
       trusted-origin resolver.

    It writes the same context variable the real creator does, so the proofs
    read it through exactly the same accessor and cannot drift into measuring
    two different things.
    """

    def __init__(self, app, **_ignored) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        state = scope.get("state") or {}
        actor = state.get(SCOPE_ACTOR_KEY)
        previous = _evidence_var.get()
        evidence = RequestEvidence(
            request_id=scope_request_id(scope),
            # DEFECT 1: declines to write when there is no actor.
            actor=actor if actor else previous.actor,
            # DEFECT 3: straight off the socket.
            client_address=(scope.get("client") or ("unknown",))[0],
            user_agent="",
            from_trusted_proxy=False,
        )
        # DEFECT 2: no token retained, and no reset anywhere.
        _evidence_var.set(evidence)
        await self.app(scope, receive, send)


def scope_request_id(scope) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or ()}
    return headers.get("x-request-id") or "generated"


def test_the_sequential_proof_bites_the_shape_it_was_written_for():
    """SENSITIVITY, the defect direction.

    The same measurement that
    `test_an_anonymous_request_after_an_authenticated_one_is_anonymous` makes,
    taken against the planted defect, must come out the OTHER way. If it did
    not, that test would be passing over a property it cannot see.
    """
    assert _sequential_leak_observed(
        _ErpShapedContextCreator(None)
    ), "the sequential proof did not observe the leak in the shape that has it"


def test_the_nested_overlap_proof_bites_the_shape_it_was_written_for():
    """SENSITIVITY. The overlap proof must fail against a creator that never
    tears its context down."""
    assert not _nested_overlap_preserves_the_outer_actor(
        _ErpShapedContextCreator
    ), "the overlap proof did not observe the missing teardown"


def test_the_concurrency_proof_alone_would_pass_against_the_broken_shape():
    """SENSITIVITY, in the direction that is usually left as a comment.

    `asyncio` runs every task in a COPY of the context, so two requests driven
    as two tasks are isolated whatever the code does — the isolation comes from
    the runner, not from the resets. ERP measured the same thing against
    `BaseHTTPMiddleware`, whose per-request task made its concurrency test green
    against a live cross-request identity leak.

    This is asserted rather than written in prose so that the claim is CHECKED:
    if per-task context copying ever stops holding, this fails and the comment
    above it does not quietly become false. It is also why
    `test_concurrent_requests_cannot_inherit_each_others_actor` must never be
    cited as the proof of this repair.
    """
    observed = _concurrent_actors_stay_separate(
        lambda inner: _ErpShapedContextCreator(inner)
    )
    assert observed == {"alice": "alice", "bob": "bob"}, (
        "per-task context isolation no longer holds; the concurrency proof has "
        "changed meaning and the sequential and nested proofs must be re-read"
    )


# ── structural: the resolver is not bypassed ────────────────────────────────


def peer_address_reads(source: str) -> list[str]:
    """Direct reads of the transport peer, found by AST rather than by text.

    A pure function over source so the sensitivity proof can hand it planted
    code — a checker that has only ever run over a clean tree has proved nothing
    about itself.

    AST and not a substring scan for a concrete reason: the module's own prose
    names `request.client.host` in order to explain why the creator must not
    read it, and a text scan would refuse its own rationale. That mistake has
    been made before in this programme.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "client"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"request", "scope"}
        ):
            found.append(f"{node.value.id}.client at line {node.lineno}")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "scope"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "client"
        ):
            found.append(f"scope['client'] at line {node.lineno}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "scope"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "client"
        ):
            found.append(f"scope.get('client') at line {node.lineno}")
    return found


def test_the_context_creator_does_not_read_the_peer_address_directly():
    """The behavioural tests would still pass if someone reintroduced a direct
    peer read for a case they thought was safe.

    ERP had a correct trusted-origin resolver and its own context creator
    bypassed it, so the address recorded behind a proxy was the proxy's. The
    creator must reach the peer only through `_peer_address`, whose result then
    goes through the policy.
    """
    reads = peer_address_reads(inspect.getsource(RequestEvidenceContextV1))
    assert not reads, (
        f"the context creator reads the peer directly ({reads}); it must reach "
        f"the trusted-origin resolver, which ERP had and bypassed once"
    )


def test_the_peer_read_detector_names_a_planted_read():
    """SENSITIVITY, the defect direction."""
    planted = """
        class Creator:
            async def __call__(self, scope, receive, send):
                address = scope["client"][0]
                other = scope.get("client")
                legacy = request.client.host
    """
    assert len(peer_address_reads(planted)) == 3


def test_the_peer_read_detector_ignores_the_prose_that_justifies_it():
    """SENSITIVITY, the near-miss direction, and it is the one that matters.

    This is the shape a text scan gets wrong: a creator that correctly delegates
    to the policy, while its docstring names `scope["client"]` and
    `request.client.host` to explain why. Naming it would make the guard punish
    the explanation of the rule it enforces.
    """
    near_miss = '''
        class Creator:
            """Never reads scope["client"] or request.client.host directly."""

            async def __call__(self, scope, receive, send):
                # scope["client"] is deliberately not read here
                peer = _peer_address(scope)
                address = self.trusted_proxies.client_address(peer, headers)
                evidence = RequestEvidence(client_address=address)
    '''
    assert peer_address_reads(near_miss) == []


def test_the_single_seam_that_may_read_the_peer_is_the_only_one():
    """The rule is "one seam", not "nowhere" — so the seam has to be named.

    `_peer_address` must read the peer, or nothing would. Asserting the detector
    fires on it proves the module-wide absence above is a real constraint on the
    creator rather than an accident of a module that never touches the scope.
    """
    from dotmac_kernel import request_evidence

    assert peer_address_reads(inspect.getsource(request_evidence._peer_address))
