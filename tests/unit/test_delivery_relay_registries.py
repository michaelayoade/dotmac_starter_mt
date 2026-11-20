"""K-8 registries: install/active, ownership, ordering and the restore-safe
fixture (SQLite lane; the transport itself is on PG)."""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test arguments)

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import delivery_relay
from dotmac_kernel.delivery import DeliveryTerminal
from dotmac_kernel.delivery_providers import AttachmentRef
from dotmac_kernel.delivery_relay import (
    AttachmentContext,
    AttachmentResolverRegistry,
    DispatchRelayTransport,
    DuplicateRelayRegistrationError,
    ProjectionHookRegistry,
    RelayRegistryNotInstalledError,
    ResolvedAttachments,
    UnknownAttachmentKind,
    active_attachment_resolvers,
    active_projection_hooks,
)
from dotmac_kernel.messaging.relay import ClaimedEvent

from tests.delivery_relay_support import relay_registries  # noqa: F401  (fixture)

REF = AttachmentRef(
    kind="document", ref="r1", filename="a.pdf", content_type="application/pdf"
)


def _resolved(registry) -> ResolvedAttachments:
    return ResolvedAttachments(
        db=None,  # type: ignore[arg-type]
        tenant_id=uuid4(),
        dispatch_id=uuid4(),
        registry=registry,
    )


def test_resolvers_are_keyed_by_kind_and_have_one_owner() -> None:
    reg = AttachmentResolverRegistry([("academy", "document", lambda c: b"x")])
    assert reg.kinds() == frozenset({"document"}) and reg.owner("document") == "academy"
    with pytest.raises(UnknownAttachmentKind):
        reg.resolver_for("storage")
    with pytest.raises(DuplicateRelayRegistrationError):
        AttachmentResolverRegistry(
            [("a", "document", lambda c: b"x"), ("b", "document", lambda c: b"y")]
        )


def test_resolved_attachments_resolve_lazily_once_and_reject_empty_bytes() -> None:
    calls: list[AttachmentContext] = []

    def doc(ctx: AttachmentContext) -> bytes:
        calls.append(ctx)
        return b"bytes"

    resolved = _resolved(AttachmentResolverRegistry([("p", "document", doc)]))
    assert calls == []  # nothing runs until the provider asks
    assert resolved(REF) == resolved(REF) == b"bytes"
    assert len(calls) == 1 and calls[0].ref == REF

    empty = _resolved(AttachmentResolverRegistry([("p", "document", lambda c: b"")]))
    with pytest.raises(UnknownAttachmentKind):
        empty(REF)


def test_hooks_filter_by_category_keep_order_and_names_are_unique() -> None:
    def a(c):
        return None

    def b(c):
        return None

    reg = ProjectionHookRegistry(
        [("p", "first", a, None), ("p", "second", b, {"academy.invite"})]
    )
    assert reg.names() == ("first", "second")
    assert [n for n, _ in reg.hooks_for("academy.invite")] == ["first", "second"]
    assert [n for n, _ in reg.hooks_for("academy.digest")] == ["first"]
    with pytest.raises(DuplicateRelayRegistrationError):
        ProjectionHookRegistry([("p", "x", a, None), ("q", "x", b, None)])
    with pytest.raises(ValueError):
        ProjectionHookRegistry([("p", "x", a, set())])


def test_an_uninstalled_registry_is_refused_not_treated_as_empty(monkeypatch) -> None:
    monkeypatch.setattr(delivery_relay, "_active_resolvers", None)
    monkeypatch.setattr(delivery_relay, "_active_hooks", None)
    with pytest.raises(RelayRegistryNotInstalledError):
        active_attachment_resolvers()
    with pytest.raises(RelayRegistryNotInstalledError):
        active_projection_hooks()


def test_the_fixture_installs_empty_registries_and_restores_the_previous(
    relay_registries,
) -> None:
    assert active_attachment_resolvers().kinds() == frozenset()
    assert active_projection_hooks().names() == ()


def test_the_transport_refuses_foreign_events_and_malformed_payloads() -> None:
    transport = DispatchRelayTransport(provider_factory=lambda r: None)  # type: ignore[arg-type,return-value]
    base = {"tenant_id": uuid4(), "attempts": 0, "correlation_id": None}
    wrong = ClaimedEvent(
        id=uuid4(), event_type="other.v1", payload={"dispatch_id": str(uuid4())}, **base
    )
    with pytest.raises(DeliveryTerminal, match="unexpected event type"):
        transport.deliver(wrong, None)  # type: ignore[arg-type]
    for payload in ({}, {"dispatch_id": "nope"}, {"dispatch_id": str(uuid4()), "x": 1}):
        event = ClaimedEvent(
            id=uuid4(), event_type="delivery.dispatch.v1", payload=payload, **base
        )
        with pytest.raises(DeliveryTerminal, match="malformed"):
            transport.deliver(event, None)  # type: ignore[arg-type]
        transport.dead_letter(event, None)  # type: ignore[arg-type]  # no dispatch: no-op
