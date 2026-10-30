"""Hosted PostgreSQL first-dispatch, crash/retry, and immutable identity proofs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from dotmac_integration import InboundEvent, InboxReceipt
from dotmac_integration.capability_registry import capability_registry
from dotmac_integration.destination_binding import (
    LocalScope,
    ProductPortDescriptorSnapshot,
    product_port_descriptor_digest,
    reconcile_product_port_descriptor,
)
from dotmac_integration.ingress import prepare_ingress, record_batch
from dotmac_integration.models import CapabilityBinding, ConnectorInstallation
from dotmac_integration.receipt_delivery import (
    DeliveryPreparationRefused,
    ProductAcceptance,
    ProductOutcome,
    ReceiptClaims,
    build_product_request,
    deliver_receipt,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.test_integration_isolation import (  # noqa: F401
    _installed_capability_registry,
    _superuser_url,
    _url_for,
    migrated_scratch,
)
from tests.unit.test_integration_ingress import (  # noqa: F401
    IngressFake,
    _installed_integration_audit_actions,
    address,
    build,
    registry_for,
)


@pytest.fixture
def dispatch(migrated_scratch):  # noqa: F811
    engine = create_engine(migrated_scratch[0])
    registry = registry_for(IngressFake())
    capabilities = capability_registry()
    with Session(engine) as db, db.begin():
        installation, binding, key = build(db, registry)
        prepared = prepare_ingress(db, endpoint=address(key), registry=registry)
        ((receipt_id, _),) = record_batch(
            db,
            prepared,
            (
                InboundEvent(
                    provider_event_id=uuid4().hex,
                    event_type="received",
                    payload={"value": 1},
                ),
            ),
        )
        contract = capabilities.get(binding.capability_id)
        descriptor = ProductPortDescriptorSnapshot(
            schema_version="dotmac.io/product-port-descriptor/v3",
            application=contract.owner.application,
            owner_module=contract.owner.module,
            capability_id=contract.capability_id,
            capability_summary=contract.summary,
            contract_version=contract.contract_version,
            destination_binding_id=uuid4(),
            delivery_path="/integration/observations",
            mirror_path="/integration/mirror",
            destination_scope=LocalScope("inbox", "snapshot-test"),
            activation_state="enabled",
            source_revision="a" * 64,
            descriptor_digest="0" * 64,
            wire_schema_version="dotmac.io/product-observation/v2",
            capability_contract=contract,
        )
        descriptor = replace(
            descriptor, descriptor_digest=product_port_descriptor_digest(descriptor)
        )
        reconcile_product_port_descriptor(
            db,
            capability_binding_id=binding.id,
            descriptor=descriptor,
            registry=capabilities,
        )
        binding_id, installation_id = binding.id, installation.id
    sessions = []
    factory = sessionmaker(bind=engine)

    def session_factory():
        session = factory()
        sessions.append(session)
        return session

    store = ReceiptClaims(session_factory, registry=capabilities)
    yield (
        engine,
        store,
        receipt_id,
        binding_id,
        installation_id,
        descriptor,
        capabilities,
        sessions,
    )
    engine.dispose()


def _expire(engine, receipt_id):
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts SET leased_until=now()-interval '1 "
                "second', next_attempt_at=NULL WHERE id=:id"
            ),
            {"id": receipt_id},
        )


def test_receiver_commit_then_crash_retries_identical_contract_without_transaction(
    dispatch,
):
    engine, store, receipt_id, _, _, _, _, sessions = dispatch
    seen = []
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE mod_intg.test_snapshot_effects (key text PRIMARY KEY, "
                "fingerprint text NOT NULL)"
            )
        )

    class Receiver:
        def deliver(self, request):
            assert not any(session.in_transaction() for session in sessions)
            seen.append(request)
            with engine.begin() as conn:
                result = conn.execute(
                    text(
                        "INSERT INTO mod_intg.test_snapshot_effects VALUES "
                        "(:key,:fp) ON CONFLICT DO NOTHING"
                    ),
                    {"key": request.idempotency_key, "fp": request.request_fingerprint},
                )
                inserted = result.rowcount == 1
            if len(seen) == 1:
                raise RuntimeError("worker died after receiver commit")
            return ProductOutcome(
                ProductAcceptance.ACCEPTED
                if inserted
                else ProductAcceptance.ALREADY_APPLIED
            )

    try:
        with pytest.raises(RuntimeError, match="receiver commit"):
            deliver_receipt(receipt_id=receipt_id, store=store, gateway=Receiver())
        with Session(engine) as db:
            row = db.get(InboxReceipt, receipt_id)
            assert row.delivery_snapshot_json["state"] == "frozen"
            assert row.delivery_fingerprint == seen[0].request_fingerprint
            assert row.delivery_idempotency_key == seen[0].idempotency_key
            assert row.correlation_id == seen[0].correlation_id
        _expire(engine, receipt_id)
        report = deliver_receipt(receipt_id=receipt_id, store=store, gateway=Receiver())
        assert report.outcome.acceptance is ProductAcceptance.ALREADY_APPLIED
        assert seen[0] == seen[1]
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT count(*) FROM mod_intg.test_snapshot_effects"))
                == 1
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE mod_intg.test_snapshot_effects"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivery_path", "/integration/changed"),
        ("destination_binding_id", uuid4()),
        ("destination_scope", LocalScope("inbox", "changed")),
        ("wire_schema_version", "dotmac.io/product-observation/v1"),
        ("activation_state", "retired"),
    ],
)
def test_changed_live_route_refuses_before_gateway(dispatch, field, value):
    engine, store, receipt_id, binding_id, _, descriptor, capabilities, _ = dispatch
    first = store.claim(receipt_id=receipt_id)
    original = build_product_request(first)
    changed = replace(descriptor, **{field: value})
    changed = replace(
        changed, descriptor_digest=product_port_descriptor_digest(changed)
    )
    with Session(engine) as db, db.begin():
        reconcile_product_port_descriptor(
            db,
            capability_binding_id=binding_id,
            descriptor=changed,
            registry=capabilities,
        )
    _expire(engine, receipt_id)

    class Gateway:
        def deliver(self, request):
            pytest.fail("changed authority must never reroute")

    report = deliver_receipt(receipt_id=receipt_id, store=store, gateway=Gateway())
    assert report.preparation_refused
    assert report.outcome.error_code == "dispatch_authority_changed"
    with Session(engine) as db:
        row = db.get(InboxReceipt, receipt_id)
        assert row.delivery_fingerprint == original.request_fingerprint
        assert row.state == "reconciliation_required"


@pytest.mark.parametrize("owner", [CapabilityBinding, ConnectorInstallation])
def test_disabled_live_authority_refuses(dispatch, owner):
    engine, store, receipt_id, binding_id, installation_id, *_ = dispatch
    store.claim(receipt_id=receipt_id)
    with Session(engine) as db, db.begin():
        db.get(
            owner, binding_id if owner is CapabilityBinding else installation_id
        ).state = "disabled"
    _expire(engine, receipt_id)
    with pytest.raises(DeliveryPreparationRefused, match="dispatch_authority_changed"):
        store.claim(receipt_id=receipt_id)


def test_concurrent_real_claims_freeze_once_and_stale_worker_cannot_settle(dispatch):
    engine, store, receipt_id, *_ = dispatch
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=20)
        return store.claim(receipt_id=receipt_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]
    winner = next(item for item in results if item is not None)
    assert sum(item is not None for item in results) == 1
    _expire(engine, receipt_id)
    retry = store.claim(receipt_id=receipt_id)
    assert build_product_request(winner) == build_product_request(retry)
    assert not store.settle(winner, ProductOutcome(ProductAcceptance.REJECTED))
    assert store.settle(retry, ProductOutcome(ProductAcceptance.ACCEPTED))


@pytest.mark.parametrize(
    "column,expression",
    [
        ("delivery_snapshot_json", "NULL"),
        ("delivery_fingerprint", "repeat('b',64)"),
        ("delivery_idempotency_key", "'changed'"),
        ("correlation_id", "'changed'"),
        ("destination_application", "'changed'"),
        ("destination_revision_id", "NULL"),
        ("destination_contract_version", "99"),
        ("provider_event_id", "'changed'"),
        ("event_type", "'changed'"),
        ("payload_digest", "repeat('b',64)"),
        ("payload_json", "'{\"value\": 2}'::jsonb"),
    ],
)
def test_direct_sql_frozen_identity_tamper_is_rejected(dispatch, column, expression):
    engine, store, receipt_id, *_ = dispatch
    store.claim(receipt_id=receipt_id)
    with pytest.raises(DBAPIError, match="dispatch identity is immutable"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE mod_intg.inbox_receipts SET {column}={expression} "  # noqa: S608 -- parametrized module-owned SQL fixtures only
                    "WHERE id=:id"
                ),
                {"id": receipt_id},
            )


def test_failed_preparation_commits_refusal_but_never_half_frozen_identity(dispatch):
    engine, store, receipt_id, binding_id, *_ = dispatch
    with Session(engine) as db, db.begin():
        db.get(CapabilityBinding, binding_id).state = "disabled"
    with pytest.raises(DeliveryPreparationRefused):
        store.claim(receipt_id=receipt_id)
    with Session(engine) as db:
        row = db.get(InboxReceipt, receipt_id)
        assert row.delivery_snapshot_json == {"schema_version": 1, "state": "unstarted"}
        assert row.delivery_fingerprint is row.delivery_idempotency_key is None
        assert row.state == "reconciliation_required"


def test_claim_commit_failure_rolls_back_lease_and_freeze(dispatch):
    engine, _, receipt_id, _, _, _, capabilities, _ = dispatch

    class FailCommit(Session):
        def commit(self):
            raise RuntimeError("commit failed")

    store = ReceiptClaims(
        sessionmaker(bind=engine, class_=FailCommit), registry=capabilities
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        store.claim(receipt_id=receipt_id)
    with Session(engine) as db:
        row = db.get(InboxReceipt, receipt_id)
        assert row.delivery_snapshot_json["state"] == "unstarted"
        assert row.delivery_fingerprint is row.leased_until is None
        assert row.attempt_count == 0


def test_replay_preserves_frozen_snapshot_and_retention_never_resurrects(dispatch):
    from dotmac_integration.operations import replay_receipt

    engine, store, receipt_id, *_ = dispatch
    first = store.claim(receipt_id=receipt_id)
    assert store.settle(first, ProductOutcome(ProductAcceptance.REJECTED))
    with Session(engine) as db, db.begin():
        row = db.get(InboxReceipt, receipt_id)
        snapshot = row.delivery_snapshot_json
        replay_receipt(db, row, actor_admin_id=None, reason="hosted synthetic replay")
        assert row.attempt_count == 0
        assert row.delivery_snapshot_json == snapshot
    replay = store.claim(receipt_id=receipt_id)
    assert build_product_request(replay) == build_product_request(first)
    assert store.settle(replay, ProductOutcome(ProductAcceptance.ACCEPTED))
    with engine.begin() as conn:
        # Shape produced by the existing retention owner: sole tombstone key.
        conn.execute(
            text("""
            UPDATE mod_intg.inbox_receipts SET payload_json=jsonb_build_object(
                '__dotmac_redacted__', jsonb_build_object(
                'payload_digest',payload_digest,
                'redacted_at',now(),'retention_days',1,
                'legal_policy_owner','test','key_count',1))
            WHERE id=:id
        """),
            {"id": receipt_id},
        )
    with pytest.raises(DBAPIError, match="dispatch identity is immutable"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE mod_intg.inbox_receipts "
                    "SET payload_json=CAST(:payload AS jsonb) WHERE id=:id"
                ),
                {"id": receipt_id, "payload": '{"value":1}'},
            )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_intg.inbox_receipts SET state='verified', "
                "attempt_count=0 WHERE id=:id"
            ),
            {"id": receipt_id},
        )
    with pytest.raises(
        DeliveryPreparationRefused, match="dispatch_payload_unavailable"
    ):
        store.claim(receipt_id=receipt_id)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM mod_intg.inbox_receipts WHERE id=:id"), {"id": receipt_id}
        )


def test_same_revision_uuid_with_modified_historical_route_refuses(dispatch):
    engine, store, receipt_id, *_ = dispatch
    first = store.claim(receipt_id=receipt_id)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_intg.capability_destination_revisions SET "
                "scope_ref='mutated' WHERE id=:id"
            ),
            {"id": first.destination.destination_revision_id},
        )
    _expire(engine, receipt_id)
    with pytest.raises(DeliveryPreparationRefused, match="dispatch_authority_changed"):
        store.claim(receipt_id=receipt_id)


def test_changed_live_capability_declaration_refuses_without_refresh(dispatch):
    from dotmac_integration.capability_registry import CapabilityRegistry

    engine, store, receipt_id, _, _, descriptor, capabilities, _ = dispatch
    first = store.claim(receipt_id=receipt_id)
    contract = capabilities.get(descriptor.capability_id)
    store._registry = CapabilityRegistry.from_declarations(
        [
            replace(contract, summary="Changed owner declaration"),
        ]
    )
    _expire(engine, receipt_id)
    with pytest.raises(DeliveryPreparationRefused, match="dispatch_authority_changed"):
        store.claim(receipt_id=receipt_id)
    with Session(engine) as db:
        assert (
            db.get(InboxReceipt, receipt_id).delivery_snapshot_json
            == first.delivery_snapshot.document()
        )


def test_changed_live_source_refuses_before_network(dispatch):
    engine, store, receipt_id, _, installation_id, *_ = dispatch
    store.claim(receipt_id=receipt_id)
    with Session(engine) as db, db.begin():
        db.get(ConnectorInstallation, installation_id).connector_key = "changed"
    _expire(engine, receipt_id)
    with pytest.raises(DeliveryPreparationRefused, match="dispatch_authority_changed"):
        store.claim(receipt_id=receipt_id)


def test_populated_downgrade_cannot_erase_dispatch_identity(
    dispatch,
    migrated_scratch,  # noqa: F811
):
    from alembic import command
    from tests.test_integration_receipt_origin_migration import _config

    engine, store, receipt_id, *_ = dispatch
    store.claim(receipt_id=receipt_id)
    with pytest.raises(
        DBAPIError, match="cannot erase frozen receipt dispatch identity"
    ):
        command.downgrade(_config(migrated_scratch[0]), "ig_0017_provisioning")
    with Session(engine) as db:
        assert (
            db.get(InboxReceipt, receipt_id).delivery_snapshot_json["state"] == "frozen"
        )


def test_platform_role_and_before_trigger_cannot_tamper_with_frozen_identity(dispatch):
    engine, store, receipt_id, *_ = dispatch
    store.claim(receipt_id=receipt_id)
    # app_admin cannot assume platform_api. Use the migration owner against the
    # same scratch database, then switch to the actual platform role locally.
    database = engine.url.database
    assert database is not None
    migration_owner = create_engine(_url_for(_superuser_url(), database))
    try:
        with pytest.raises(DBAPIError, match="dispatch identity is immutable"):
            with migration_owner.begin() as conn:
                conn.execute(text("SET LOCAL ROLE platform_api"))
                assert conn.scalar(text("SELECT current_user")) == "platform_api"
                conn.execute(
                    text(
                        "UPDATE mod_intg.inbox_receipts SET correlation_id='tampered' "
                        "WHERE id=:id"
                    ),
                    {"id": receipt_id},
                )
    finally:
        migration_owner.dispose()
    # The final AFTER guard sees the completed row, even when an earlier trigger
    # changes identity during an otherwise ordinary outcome update.
    with pytest.raises(DBAPIError, match="dispatch identity is immutable"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                CREATE FUNCTION mod_intg.test_mutate_dispatch() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    NEW.correlation_id := 'tampered'; RETURN NEW; END $$;
                CREATE TRIGGER test_mutate_dispatch
                BEFORE UPDATE ON mod_intg.inbox_receipts
                FOR EACH ROW EXECUTE FUNCTION mod_intg.test_mutate_dispatch();
            """)
            )
            conn.execute(
                text(
                    "UPDATE mod_intg.inbox_receipts SET error_code='ordinary' WHERE "
                    "id=:id"
                ),
                {"id": receipt_id},
            )
    # Function/trigger creation rolls back with the refused update.


def test_settlement_refuses_changed_request_identity_under_the_same_live_lease(
    dispatch,
):
    engine, store, receipt_id, *_ = dispatch
    claim = store.claim(receipt_id=receipt_id)
    outcome = ProductOutcome(ProductAcceptance.ACCEPTED, product_ref="effect")
    assert not store.settle(replace(claim, correlation_id="changed"), outcome)
    from dotmac_integration.receipt_delivery import FingerprintConflict

    with pytest.raises(FingerprintConflict):
        store.settle(replace(claim, event_type="changed"), outcome)
    with Session(engine) as db:
        row = db.get(InboxReceipt, receipt_id)
        assert row.state == "processing"
        assert row.product_ref is None
    assert store.settle(claim, outcome)


def test_schema_grace_first_dispatch_and_retry_preserve_unknown_contract_digest(
    migrated_scratch,  # noqa: F811
    request,
):
    import json
    from datetime import date

    from dotmac_integration.capability_registry import (
        CapabilityContract,
        CapabilityOwner,
        CapabilityRegistry,
        SchemaGrace,
    )
    from dotmac_integration.execution import receive_verified

    from tests.test_integration_isolation import _installation_and_binding

    engine = create_engine(migrated_scratch[0])
    grace = SchemaGrace(
        reason="hosted snapshot canary for an owner-declared schema grace",
        retire_after=date(2099, 12, 31),
        tracked_by="tests/test_integration_dispatch_snapshot.py",
    )
    contract = CapabilityContract(
        capability_id="conformance.echo.v1",
        owner=CapabilityOwner("sub", "snapshot"),
        summary="Synthetic schema grace observation",
        schema_grace=grace,
    )
    registry = CapabilityRegistry.from_declarations([contract])
    with engine.begin() as conn:
        installation_id, binding_id = _installation_and_binding(conn, request)
        conn.execute(
            text("""
                INSERT INTO mod_intg.capability_destination_revisions
                  (id, capability_binding_id, revision, application,
                   scope_kind, scope_ref, contract_version)
                VALUES (:id,:binding,1,'sub','inbox','schema-grace',1)
            """),
            {"id": uuid4(), "binding": binding_id},
        )
    with Session(engine) as db, db.begin():
        receipt, created = receive_verified(
            db,
            installation_id=installation_id,
            capability_binding_id=binding_id,
            provider_event_id=uuid4().hex,
            event_type="received",
            payload={"value": 1},
        )
        assert created
        receipt_id = receipt.id
    store = ReceiptClaims(sessionmaker(bind=engine), registry=registry)
    first = store.claim(receipt_id=receipt_id)
    original = build_product_request(first)
    document = first.delivery_snapshot.document()
    stored_contract = document["capability"]["contract"]
    assert stored_contract["contract_digest"] is None
    assert stored_contract["schema_grace"] == {
        "reason": grace.reason,
        "retire_after": "2099-12-31",
        "tracked_by": grace.tracked_by,
    }
    assert all(
        stored_contract[key] is None
        for key in (
            "command_schema",
            "result_schema",
            "observation_schema",
        )
    )
    assert store.settle(first, ProductOutcome(ProductAcceptance.UNAVAILABLE))
    _expire(engine, receipt_id)
    retry = store.claim(receipt_id=receipt_id)
    assert retry.attempt == first.attempt + 1
    assert build_product_request(retry) == original
    assert retry.delivery_snapshot.document() == document
    assert store.settle(retry, ProductOutcome(ProductAcceptance.ACCEPTED))
    with Session(engine) as db:
        assert db.get(InboxReceipt, receipt_id).delivery_snapshot_json == document

    def valid(candidate):
        with engine.connect() as conn:
            return conn.scalar(
                text(
                    "SELECT mod_intg.valid_dispatch_snapshot(CAST(:snapshot AS jsonb))"
                ),
                {"snapshot": json.dumps(candidate)},
            )

    assert valid(document) is True
    # Sensitivity: unknown grace must never acquire a fabricated payload digest,
    # and schema publication must end grace rather than coexist with it.
    for changes in (
        {"contract_digest": "a" * 64},
        {"observation_schema": {"type": "object"}},
        {"schema_grace": None},
    ):
        changed = json.loads(json.dumps(document))
        changed["capability"]["contract"].update(changes)
        assert valid(changed) is False
    published = replace(
        contract, observation_schema={"type": "object"}, schema_grace=None
    )
    published_document = replace(
        first.delivery_snapshot, capability=published
    ).document()
    assert valid(published_document) is True
    published_document["capability"]["contract"]["contract_digest"] = None
    assert valid(published_document) is False
    engine.dispose()


@pytest.mark.parametrize(
    "change", ["delivery_path", "mirror_path", "product_binding", "source"]
)
def test_actual_claim_full_snapshot_consistency_precedes_dispatch_and_settlement(
    dispatch,
    change,
):
    engine, store, receipt_id, *_ = dispatch
    claim = store.claim(receipt_id=receipt_id)
    if change == "source":
        forged = replace(claim, source=replace(claim.source, connector_key="changed"))
    else:
        field = "destination_binding_id" if change == "product_binding" else change
        value = uuid4() if change == "product_binding" else "/integration/changed"
        forged = replace(
            claim,
            destination=replace(
                claim.destination,
                product_port=replace(claim.destination.product_port, **{field: value}),
            ),
        )
        # These routing facts deliberately do not change published wire identity.
        assert (
            build_product_request(forged).request_fingerprint
            == build_product_request(claim).request_fingerprint
        )
        assert (
            build_product_request(forged).idempotency_key
            == build_product_request(claim).idempotency_key
        )
    assert not store.settle(forged, ProductOutcome(ProductAcceptance.ACCEPTED))

    class ModifiedClaimStore:
        def claim(self, **kwargs):
            return forged

        def settle(self, *args):
            pytest.fail("preparation refusal cannot settle a tampered claim")

    class Gateway:
        def deliver(self, request):
            pytest.fail("tampered routing/source cannot reach a gateway")

    report = deliver_receipt(
        receipt_id=receipt_id, store=ModifiedClaimStore(), gateway=Gateway()
    )
    assert report.preparation_refused
    assert report.outcome.error_code == "dispatch_identity_conflict"
    with Session(engine) as db:
        row = db.get(InboxReceipt, receipt_id)
        assert row.state == "processing"
        assert row.product_ref is None
    assert store.settle(claim, ProductOutcome(ProductAcceptance.ACCEPTED))
