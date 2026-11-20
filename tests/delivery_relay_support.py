"""Shared fixtures and helpers for the delivery relay PG tests (K-8, S10).

Not a test module. Import the fixtures into a test module the way
`tests/test_outbox_deferral.py` imports its own. The audit registry is
process-global, so the fixture here restores whatever the session had.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test arguments)

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from dotmac_kernel import audit_actions, delivery_relay
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    AuditActionsNotInstalledError,
    active_audit_actions,
    install_audit_actions,
)
from dotmac_kernel.delivery_dispatch import create_dispatch
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_FAILED,
)
from dotmac_kernel.delivery_providers import (
    FEATURE_ATTACHMENTS,
    FEATURE_HTML,
    OutboundMessage,
    ProviderResult,
)
from dotmac_kernel.delivery_relay import (
    AttachmentResolverRegistry,
    DispatchRelayTransport,
    ProjectionHookRegistry,
    ResolvedAttachments,
)
from dotmac_kernel.messaging.relay import RelayPolicy
from dotmac_kernel.messaging.worker import run_once
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.test_outbox_worker import engines, factories

__all__ = [
    "audit_registry",
    "cleanup",
    "dispatch_row",
    "engines",
    "factories",
    "make_due",
    "make_dispatch",
    "outbox_rows",
    "run",
]


@pytest.fixture()
def audit_registry() -> Generator[None, None, None]:
    """A Kernel-only audit registry for one test, then put back whatever the
    session had (never leak it: later suites need their composed actions)."""
    try:
        previous: AuditActionRegistry | None = active_audit_actions()
    except AuditActionsNotInstalledError:
        previous = None
    install_audit_actions(AuditActionRegistry.from_manifests(()))
    try:
        yield
    finally:
        if previous is not None:
            install_audit_actions(previous)
        else:
            audit_actions._active_registry = None


@pytest.fixture()
def cleanup(admin_session, tenant_a, tenant_b):
    yield
    admin_session.rollback()
    for table in (
        "outbox_events",
        "audit_events",
        "communication_deliveries",
        "communication_suppressions",
        "communication_dispatches",
    ):
        for t in (tenant_a, tenant_b):
            admin_session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                {"t": str(t.id)},
            )
    admin_session.commit()


def make_dispatch(
    factories,
    tenant,
    *,
    attachments=(),
    category="transactional",
    address="jane@example.com",
) -> uuid.UUID:
    """Create a dispatch (and its outbox event) as `app_user` under tenant RLS."""
    _, tenant_sm = factories
    db = tenant_sm()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant.id)},
        )
        res = create_dispatch(
            db,
            tenant.id,
            idempotency_key=f"k-{uuid.uuid4().hex[:10]}",
            on_conflict="raise_if_different",
            channel="email",
            category=category,
            address=address,
            template_provenance="template:welcome@r1",
            subject="Hello",
            text="body text",
            attachments=attachments,
        )
        db.commit()
        return res.dispatch_id
    finally:
        db.close()


def dispatch_row(admin_session: Session, dispatch_id: uuid.UUID):
    admin_session.rollback()
    return admin_session.execute(
        text(
            "SELECT status, terminal_at FROM communication_dispatches "
            "WHERE dispatch_id = :d"
        ),
        {"d": str(dispatch_id)},
    ).one()


def outbox_rows(admin_session: Session, tenant) -> list:
    admin_session.rollback()
    return list(
        admin_session.execute(
            text(
                "SELECT id, status, attempts, last_error, payload "
                "FROM outbox_events WHERE tenant_id = :t ORDER BY created_at"
            ),
            {"t": str(tenant.id)},
        ).all()
    )


def make_due(admin_session: Session) -> None:
    admin_session.execute(text("UPDATE outbox_events SET available_at = now()"))
    admin_session.commit()


def run(factories, transport, policy: RelayPolicy = RelayPolicy()) -> int:
    disp_sm, tenant_sm = factories
    ddb = disp_sm()
    try:
        return run_once(
            dispatcher_db=ddb,
            tenant_session_factory=tenant_sm,
            transport=transport,
            worker_id="w1",
            policy=policy,
        )
    finally:
        ddb.close()


# --------------------------------------------------------------------------
# K-8 transport support
# --------------------------------------------------------------------------
@pytest.fixture()
def relay_registries() -> Generator[None, None, None]:
    """Process-global resolver/hook registries: start each test empty-installed
    and put back whatever the session had (never leak one test's registry)."""
    prev = (delivery_relay._active_resolvers, delivery_relay._active_hooks)
    delivery_relay.install_attachment_resolvers(AttachmentResolverRegistry(()))
    delivery_relay.install_projection_hooks(ProjectionHookRegistry(()))
    try:
        yield
    finally:
        delivery_relay._active_resolvers, delivery_relay._active_hooks = prev


class FakeProvider:
    """A scripted provider. `script` is a list of outcomes consumed per send, the
    last one repeating: a status string, or an exception instance to raise."""

    name = "fake"
    message_features = frozenset({FEATURE_HTML, FEATURE_ATTACHMENTS})
    message_headers = frozenset()

    def __init__(self, script, resolved: ResolvedAttachments | None = None) -> None:
        self.script = script
        self.resolved = resolved
        self.sent: list[OutboundMessage] = []
        self.attachment_bytes: list[bytes] = []

    def send(self, message: OutboundMessage) -> ProviderResult:
        step = self.script[min(len(self.sent), len(self.script) - 1)]
        self.sent.append(message)
        if isinstance(step, BaseException):
            raise step
        for ref in message.attachments:
            assert self.resolved is not None
            self.attachment_bytes.append(self.resolved(ref))
        return ProviderResult(
            status=step, provider_message_id="pm-1", response_code="250"
        )


class Harness:
    """One transport over one scripted provider, rebuilt per delivery with the
    delivery's `ResolvedAttachments` the way a product's factory would be."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.provider = FakeProvider(self.script)
        self.transport = DispatchRelayTransport(provider_factory=self._factory)

    def _factory(self, resolved: ResolvedAttachments) -> FakeProvider:
        self.provider.resolved = resolved
        return self.provider

    @property
    def calls(self) -> int:
        return len(self.provider.sent)


__all__ += [
    "DELIVERY_ACCEPTED",
    "DELIVERY_FAILED",
    "FakeProvider",
    "Harness",
    "relay_registries",
]
