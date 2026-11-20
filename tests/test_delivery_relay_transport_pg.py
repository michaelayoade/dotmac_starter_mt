"""K-8 end to end on real Postgres: the `delivery.dispatch.v1` transport with a
scripted fake provider, run through the real worker as `outbox_dispatcher` +
`app_user` (the two roles production uses).

The matrix: accepted, suppressed, deferred, failed -> retry, bounced, unknown ->
terminal, attempts exhausted -> dead (and S8's requeue back), plus the
id-only payload, lazy attachment resolution in the tenant session and
projection hooks that fail after a recorded send.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test arguments)

from __future__ import annotations

import uuid

import pytest
from dotmac_delivery_smtp import SmtpOutcomeUnknown
from dotmac_kernel.delivery import DeliveryDeferred, DeliveryOutcomeUnknown
from dotmac_kernel.delivery_providers import AttachmentRef
from dotmac_kernel.delivery_relay import (
    AUDIT_ACTION_OUTCOME_UNKNOWN,
    AttachmentResolverRegistry,
    ProjectionHookRegistry,
)
from dotmac_kernel.delivery_requeue import requeue_dead_dispatch
from dotmac_kernel.messaging.relay import RelayPolicy
from sqlalchemy import text

from tests.delivery_relay_support import (  # noqa: F401  (fixtures + helpers)
    Harness,
    audit_registry,
    cleanup,
    dispatch_row,
    engines,
    factories,
    make_dispatch,
    make_due,
    outbox_rows,
    relay_registries,
    run,
)

POLICY = RelayPolicy(max_attempts=3)


def _count(admin_session, sql: str, **params) -> int:
    admin_session.rollback()
    return admin_session.execute(text(sql), params).scalar_one()


def _receipts(admin_session, did) -> list[str]:
    admin_session.rollback()
    return [
        r.status
        for r in admin_session.execute(
            text(
                "SELECT status FROM communication_deliveries "
                "WHERE dispatch_id = :d ORDER BY created_at"
            ),
            {"d": str(did)},
        )
    ]


# ------------------------------------------------------------------ accepted
def test_accepted_marks_sent_and_runs_projection_hooks_once(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    from dotmac_kernel import delivery_relay

    seen = []
    delivery_relay.install_projection_hooks(
        ProjectionHookRegistry(
            [
                ("academy", "entrance_invite", seen.append, {"transactional"}),
                ("academy", "digest_only", lambda c: seen.append("WRONG"), {"digest"}),
            ]
        )
    )
    did = make_dispatch(factories, tenant_a)
    h = Harness(["accepted"])

    assert run(factories, h.transport, POLICY) == 1

    assert h.calls == 1
    assert dispatch_row(admin_session, did).status == "sent"
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("sent", 0)
    assert _receipts(admin_session, did) == ["accepted"]
    (ctx,) = seen
    assert ctx.dispatch_id == did and ctx.tenant_id == tenant_a.id
    assert ctx.receipt_status == "accepted" and ctx.provider_message_id == "pm-1"
    assert ctx.template_provenance == "template:welcome@r1"


def test_the_outbox_payload_is_only_the_dispatch_id_and_carries_no_pii(
    admin_session, tenant_a, factories, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a, address="jane.secret@example.com")
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.payload == {"dispatch_id": str(did)}
    dump = _count(
        admin_session,
        "SELECT count(*) FROM outbox_events "
        "WHERE tenant_id = :t AND (payload::text ILIKE '%secret%' "
        "OR payload::text ILIKE '%body text%' OR coalesce(last_error,'') <> '')",
        t=str(tenant_a.id),
    )
    assert dump == 0


def test_attachments_resolve_lazily_in_the_tenant_session(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    from dotmac_kernel import delivery_relay

    contexts = []

    def doc(ctx):
        contexts.append(
            ctx.db.execute(
                text("SELECT current_setting('app.current_tenant', true)")
            ).scalar()
        )
        return b"%PDF-1.7 report " + ctx.ref.ref.encode()

    delivery_relay.install_attachment_resolvers(
        AttachmentResolverRegistry([("academy", "document", doc)])
    )
    ref = AttachmentRef(
        kind="document",
        ref="cert-1",
        filename="cert.pdf",
        content_type="application/pdf",
    )
    did = make_dispatch(factories, tenant_a, attachments=[ref])
    h = Harness(["accepted"])
    run(factories, h.transport, POLICY)
    assert h.provider.attachment_bytes == [b"%PDF-1.7 report cert-1"]
    assert contexts == [str(tenant_a.id)]  # resolver ran as THIS tenant
    assert dispatch_row(admin_session, did).status == "sent"


def test_unresolvable_attachment_retries_not_unknown(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    ref = AttachmentRef(
        kind="document", ref="x", filename="x.pdf", content_type="application/pdf"
    )
    did = make_dispatch(factories, tenant_a, attachments=[ref])
    h = Harness(["accepted"])  # no resolver registered for "document"
    run(factories, h.transport, POLICY)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("pending", 1)
    assert dispatch_row(admin_session, did).status == "pending"


# ---------------------------------------------------------------- suppressed
def test_suppressed_settles_without_a_provider_call_or_receipt(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    admin_session.execute(
        text(
            "INSERT INTO communication_suppressions (id, tenant_id, channel, "
            "address, scope, reason) VALUES (:i, :t, 'email', 'jane@example.com', "
            "'all', 'bounce')"
        ),
        {"i": str(uuid.uuid4()), "t": str(tenant_a.id)},
    )
    admin_session.commit()
    h = Harness(["accepted"])
    run(factories, h.transport, POLICY)
    assert h.calls == 0 and _receipts(admin_session, did) == []
    assert dispatch_row(admin_session, did).status == "suppressed"
    assert outbox_rows(admin_session, tenant_a)[0].status == "sent"


# ------------------------------------------------------------------ deferred
def test_deferred_reschedules_without_charging_or_a_receipt(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    h = Harness([DeliveryDeferred("mail gate held", retry_after=30)])
    run(factories, h.transport, POLICY)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("pending", 0)
    assert row.last_error == "deferred:1: mail gate held"
    assert dispatch_row(admin_session, did).status == "pending"
    assert _receipts(admin_session, did) == []


# ------------------------------------------------------------ failed -> retry
def test_transient_failure_keeps_its_receipt_retries_then_succeeds(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    h = Harness(["failed", "accepted"])

    run(factories, h.transport, POLICY)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("pending", 1)
    assert "250" in row.last_error and "jane" not in row.last_error
    assert dispatch_row(admin_session, did).status == "pending"
    assert _receipts(admin_session, did) == ["failed"]  # evidence survives

    make_due(admin_session)
    run(factories, h.transport, POLICY)  # K-2: a failed receipt allows a retry
    assert h.calls == 2
    assert dispatch_row(admin_session, did).status == "sent"
    assert sorted(_receipts(admin_session, did)) == ["accepted", "failed"]
    assert outbox_rows(admin_session, tenant_a)[0].status == "sent"


def test_unexpected_exceptions_retry_and_never_echo_their_text(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    make_dispatch(factories, tenant_a)
    h = Harness([RuntimeError("550 jane@example.com mailbox gone; body text")])
    run(factories, h.transport, POLICY)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "pending" and row.attempts == 1
    assert row.last_error == "TransientProviderFailure('send raised RuntimeError')"


# ------------------------------------------------------------------- bounced
@pytest.mark.parametrize("verdict", ["bounced", "rejected"])
def test_a_final_negative_verdict_fails_the_dispatch_and_stops(
    verdict,
    admin_session,
    tenant_a,
    factories,
    audit_registry,
    relay_registries,
    cleanup,
) -> None:
    did = make_dispatch(factories, tenant_a)
    h = Harness([verdict])
    run(factories, h.transport, POLICY)
    assert dispatch_row(admin_session, did).status == "failed"
    assert outbox_rows(admin_session, tenant_a)[0].status == "sent"  # handled
    make_due(admin_session)
    assert run(factories, h.transport, POLICY) == 0 and h.calls == 1
    suppressed = _count(
        admin_session,
        "SELECT count(*) FROM communication_suppressions WHERE tenant_id = :t",
        t=str(tenant_a.id),
    )
    assert suppressed == (1 if verdict == "bounced" else 0)


# ------------------------------------------------------------------- unknown
@pytest.mark.parametrize(
    "unknown",
    [
        DeliveryOutcomeUnknown("reply lost"),
        SmtpOutcomeUnknown("reply lost after DATA (TimeoutError)"),
    ],
)
def test_unknown_outcome_is_terminal_audited_and_never_retried(
    unknown,
    admin_session,
    tenant_a,
    factories,
    audit_registry,
    relay_registries,
    cleanup,
) -> None:
    did = make_dispatch(factories, tenant_a)
    h = Harness([unknown])

    assert run(factories, h.transport, RelayPolicy(max_attempts=8)) == 1
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("dead", 0)  # at once, not via retries
    assert row.last_error.startswith("terminal: provider outcome unknown")
    assert dispatch_row(admin_session, did).status == "dead"
    assert _receipts(admin_session, did) == []  # nothing the provider said
    audit = admin_session.execute(
        text(
            "SELECT actor_type, is_success, details FROM audit_events "
            "WHERE tenant_id = :t AND action = :a"
        ),
        {"t": str(tenant_a.id), "a": AUDIT_ACTION_OUTCOME_UNKNOWN},
    ).one()
    assert audit.actor_type == "system" and audit.is_success is False
    assert audit.details["retried"] is False
    assert "jane" not in str(audit.details)

    make_due(admin_session)
    assert run(factories, h.transport) == 0 and h.calls == 1  # no retry loop


def test_unknown_outcome_stays_dead_even_if_its_audit_write_fails(
    admin_session, tenant_a, factories, relay_registries, cleanup, monkeypatch
) -> None:
    from dotmac_kernel import audit_actions, delivery_relay
    from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions

    install_audit_actions(AuditActionRegistry.from_manifests(()))
    try:
        did = make_dispatch(factories, tenant_a)

        def boom(*a, **k):
            raise RuntimeError("audit store down")

        monkeypatch.setattr(delivery_relay, "write_audit_event", boom)
        h = Harness([DeliveryOutcomeUnknown("reply lost")])
        run(factories, h.transport, POLICY)
        assert dispatch_row(admin_session, did).status == "dead"
        assert outbox_rows(admin_session, tenant_a)[0].status == "dead"
    finally:
        audit_actions._active_registry = None


def test_the_unknown_audit_action_must_be_writable_before_any_send(
    admin_session, tenant_a, factories, relay_registries, cleanup
) -> None:
    """With no audit registry installed an unknown outcome would be invisible, so
    the transport refuses to send at all (retried; nothing reaches the provider)."""
    from dotmac_kernel import audit_actions
    from dotmac_kernel.audit_actions import (
        AuditActionsNotInstalledError,
        active_audit_actions,
    )

    try:
        previous = active_audit_actions()
    except AuditActionsNotInstalledError:
        previous = None
    audit_actions._active_registry = None
    try:
        make_dispatch(factories, tenant_a)
        h = Harness(["accepted"])
        run(factories, h.transport, POLICY)
        assert h.calls == 0
        (row,) = outbox_rows(admin_session, tenant_a)
        assert (row.status, row.attempts) == ("pending", 1)
    finally:
        audit_actions._active_registry = previous


# ----------------------------------------------------------------- dead / S8
def test_attempts_exhausted_marks_dispatch_dead_and_s8_requeue_revives_it(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    h = Harness(["failed", "failed", "accepted"])
    policy = RelayPolicy(max_attempts=2)

    run(factories, h.transport, policy)
    make_due(admin_session)
    run(factories, h.transport, policy)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("dead", 2)
    assert dispatch_row(admin_session, did).status == "dead"
    # `record_receipt` is idempotent per (provider, dispatch, status): the second
    # `failed` attempt adds no second row.
    assert _receipts(admin_session, did) == ["failed"]

    _, tenant_sm = factories
    db = tenant_sm()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant_a.id)},
        )
        res = requeue_dead_dispatch(
            db,
            tenant_a.id,
            dispatch_id=did,
            actor_id="operator-1",
            reason="provider restored",
            source_application="dotmac_starter_mt",
        )
        db.commit()
    finally:
        db.close()
    assert res.requeued and dispatch_row(admin_session, did).status == "pending"

    run(factories, h.transport, policy)  # third script step: accepted
    assert dispatch_row(admin_session, did).status == "sent"
    assert [r.status for r in outbox_rows(admin_session, tenant_a)] == ["dead", "sent"]


def test_a_dead_dispatch_is_never_sent_by_a_stray_event(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    did = make_dispatch(factories, tenant_a)
    admin_session.execute(
        text(
            "UPDATE communication_dispatches SET status='dead', terminal_at=now() "
            "WHERE dispatch_id = :d"
        ),
        {"d": str(did)},
    )
    admin_session.commit()
    h = Harness(["accepted"])
    run(factories, h.transport, POLICY)
    assert h.calls == 0
    assert outbox_rows(admin_session, tenant_a)[0].status == "dead"


# --------------------------------------------------------- malformed payloads
@pytest.mark.parametrize(
    "payload",
    [
        '{"dispatch_id": "%s", "address": "jane@example.com"}',
        '{"dispatch_id": "not-a-uuid"}',
        "{}",
    ],
)
def test_a_payload_that_is_not_exactly_the_dispatch_id_is_refused(
    payload,
    admin_session,
    tenant_a,
    factories,
    audit_registry,
    relay_registries,
    cleanup,
) -> None:
    did = make_dispatch(factories, tenant_a)
    admin_session.execute(
        text("UPDATE outbox_events SET payload = CAST(:p AS jsonb)"),
        {"p": payload % did if "%s" in payload else payload},
    )
    admin_session.commit()
    h = Harness(["accepted"])
    run(factories, h.transport, POLICY)
    assert h.calls == 0
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "dead" and row.last_error.startswith("terminal: malformed")
    assert dispatch_row(admin_session, did).status == "pending"  # untouched


# ------------------------------------------------------------ projection hooks
def test_a_failing_hook_retries_only_the_hooks_never_the_send(
    admin_session, tenant_a, factories, audit_registry, relay_registries, cleanup
) -> None:
    from dotmac_kernel import delivery_relay

    state = {"fail": True, "ran": 0}

    def hook(ctx):
        state["ran"] += 1
        ctx.db.execute(text("SELECT 1"))
        if state["fail"]:
            raise RuntimeError("projection store down: jane@example.com")

    delivery_relay.install_projection_hooks(
        ProjectionHookRegistry([("academy", "entrance_invite", hook, None)])
    )
    did = make_dispatch(factories, tenant_a)
    h = Harness(["accepted"])

    run(factories, h.transport, POLICY)
    (row,) = outbox_rows(admin_session, tenant_a)
    assert (row.status, row.attempts) == ("pending", 1)
    assert "jane" not in row.last_error and "entrance_invite" in row.last_error
    assert dispatch_row(admin_session, did).status == "sent"  # send is durable
    assert _receipts(admin_session, did) == ["accepted"]

    state["fail"] = False
    make_due(admin_session)
    run(factories, h.transport, POLICY)
    assert h.calls == 1  # never re-sent
    assert state["ran"] == 2
    assert outbox_rows(admin_session, tenant_a)[0].status == "sent"


# ------------------------------------------------------------- tenant scoping
def test_the_transport_only_loads_its_own_tenants_dispatch(
    admin_session,
    tenant_a,
    tenant_b,
    factories,
    audit_registry,
    relay_registries,
    cleanup,
) -> None:
    did_b = make_dispatch(factories, tenant_b)
    # An event in tenant A naming tenant B's dispatch: RLS makes it not found.
    admin_session.execute(
        text(
            "INSERT INTO outbox_events (id, tenant_id, event_type, status, payload) "
            "VALUES (:i, :t, 'delivery.dispatch.v1', 'pending', CAST(:p AS jsonb))"
        ),
        {
            "i": str(uuid.uuid4()),
            "t": str(tenant_a.id),
            "p": f'{{"dispatch_id": "{did_b}"}}',
        },
    )
    admin_session.execute(
        text(
            "UPDATE outbox_events SET status='sent' WHERE tenant_id = :t AND "
            "payload->>'dispatch_id' = :d AND tenant_id = :b"
        ),
        {"t": str(tenant_b.id), "d": str(did_b), "b": str(tenant_b.id)},
    )
    admin_session.commit()
    h = Harness(["accepted"])
    run(factories, h.transport, POLICY)
    assert h.calls == 0
    assert dispatch_row(admin_session, did_b).status == "pending"
    (row,) = outbox_rows(admin_session, tenant_a)
    assert row.status == "dead" and row.last_error == "terminal: dispatch not found"
